#!/usr/bin/env python3

"""
BugBooth
Copyright (C) 2019 Ben Nahill <bnahill@gmail.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import sys
import time
import socket
import struct
import threading
import queue
import os
import subprocess
import configparser
import argparse
import mimetypes
import random

from PIL import Image

from typing import Union, Optional, List, Dict, Tuple, Callable

from PyQt5.QtCore import QDir, Qt, QUrl, QIODevice, pyqtSignal, QPoint, QRect, QSize, QObject, pyqtSlot, pyqtSignal, QThread
from PyQt5.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
        QPushButton, QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget, QBoxLayout)
from PyQt5.QtWidgets import QMainWindow,QWidget, QPushButton, QAction, QGridLayout, QSpacerItem
from PyQt5.QtGui import QIcon, QImage, QPixmap, QPainter, QFont, QBitmap, QBrush, QPen, QColor, QMouseEvent

IMAGE_T = Image.Image


class BugBoothConfig:
    CountdownTimer: int
    DelayBetweenShots: int

    PhotosPerStrip: int
    BackgroundMode: str
    BackgroundPath: List[str]

    Arrangement: str
    Margins: Tuple[int, int, int, int]

    ThumbnailWidth: int
    ThumbnailX: int
    ThumbnailY: int
    ThumbnailSkipX: Optional[int]
    ThumbnailSkipY: Optional[int]

    def __init__(self, configfile: str = "bugbooth.conf") -> None:
        c = configparser.ConfigParser()
        print(f"Reading configuration from {configfile}:")
        c.read(configfile)

        try:
            self.CountdownTimer = int(c["GUI"]["CountdownTimer"])
        except (KeyError, ValueError):
            self.CountdownTimer = 3
        print(f"  Countdown timer: {self.CountdownTimer}")

        try:
            self.DelayBetweenShots = int(c["GUI"]["DelayBetweenShots"])
        except (KeyError, ValueError):
            self.DelayBetweenShots = 3
        print(f"  Delay between shots: {self.DelayBetweenShots}")

        try:
            self.PhotosPerStrip = int(c["Composition"]["PhotosPerStrip"])
        except (KeyError, ValueError):
            self.PhotosPerStrip = 4
        print(f"  Photos per strip: {self.PhotosPerStrip}")

        valid_bg_modes = ["SingleVertical", "DoubleVertical"]
        try:
            self.BackgroundMode = str(c["Composition"]["BackgroundMode"])
            if self.BackgroundMode not in valid_bg_modes:
                print(f"Invalid background mode ({self.BackgroundMode}), falling back to single")
                self.BackgroundMode = "SingleVertical"
        except (KeyError, ValueError):
            self.BackgroundMode = "SingleVertical"
        assert self.BackgroundMode in valid_bg_modes, "Only a single static background is supported at this time"
        print(f"  Background mode: {self.BackgroundMode}")

        try:
            self.ThumbnailWidth = int(c["Composition"]["ThumbnailWidth"])
            self.ThumbnailX = int(c["Composition"]["ThumbnailX"])
            self.ThumbnailY = int(c["Composition"]["ThumbnailY"])
        except (KeyError, ValueError):
            assert False, "Please provide thumbnail width and x/y coordinates"

        try:
            self.ThumbnailSkipX = int(c["Composition"]["ThumbnailSkipX"])
        except (KeyError, ValueError):
            assert self.BackgroundMode == "SingleVertical", "Selected background mode requires a ThumbnailSkipX parameter"
            self.ThumbnailSkipX = None

        try:
            self.ThumbnailSkipY = int(c["Composition"]["ThumbnailSkipY"])
        except (KeyError, ValueError):
            assert False, "Selected background mode requires a ThumbnailSkipY parameter"
            self.ThumbnailSkipY = None

        self.BackgroundPath = []
        try:
            bg_path = str(c["Composition"]["BackgroundPath"])
            if os.path.isdir(bg_path):
                self.BackgroundPath = os.listdir(bg_path)
            elif os.path.isfile(bg_path):
                self.BackgroundPath = [bg_path]

        except (KeyError, ValueError):
            assert False, "No BackgroundPath provided in configuration file"

        self.BackgroundPath = [f"{bg_path}/{x}" for x in self.BackgroundPath if self.path_is_img(x)]
        assert len(self.BackgroundPath) != 0,"The provided BackgroundPath was not found to contain an image"

        print(f"  Backgrounds: {self.BackgroundPath}")

        try:
            self.Arrangement = str(c["Print"]["Arrangement"])
            assert self.Arrangement == "2x2x6", "Currently only a pair of 2x6 strip may be generated"
        except (KeyError, ValueError):
            self.Arrangement = "2x2x6"
        print(f"  Arrangement: {self.Arrangement}")

        margins: List[int] = [0, 0, 0, 0]
        for (i, key) in zip(range(4), ["MarginTop", "MarginRight", "MarginBottom", "MarginLeft"]):
            try:
                margins[i] = int(c["Print"][key])
            except (KeyError, ValueError):
                pass
        self.Margins = tuple(margins)
        print(f"  Print margins: {self.Margins}")

    @staticmethod
    def path_is_img(path: str) -> bool:
        t: Tuple[Optional[str], Optional[str]] = mimetypes.guess_type(path)[0]
        if not isinstance(t, str):
            return False
        return t.partition("/")[0] == "image"


# A global configuration available to all of the GUI
boothconfig: Optional[BugBoothConfig] = None


class ImageReceiver(QObject):
    """ Listens to a domain socket waiting for images to come through.
    Then it calls the handler function (from its own thread).
    """

    img_received = pyqtSignal(object)
    socket_name: str
    sock: Optional[socket.socket]

    def __init__(self, socket_name: str) -> None:
        super().__init__()
        self.socket_name = socket_name
        self.sock = None

    def run(self) -> None:
        raise NotImplementedError()


class ImageReceiverDGram(ImageReceiver):
    """ Listens to a domain socket waiting for images to come through.
    Then it calls the handler function (from its own thread).
    """

    def __init__(self, socket_name: str) -> None:
        super().__init__(socket_name)

    def run(self) -> None:
        try:
            os.unlink(self.socket_name)
        except OSError:
            if os.path.exists(self.socket_name):
                raise

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.socket_name)
        print(f"Opened preview datagram socket {self.socket_name}")

        while True:
            data, addr = self.sock.recvfrom(1048576)
            if data:
                self.img_received.emit(data)
            time.sleep(0.01)


class ImageReceiverStream(ImageReceiver):
    """ Listens to a domain socket waiting for images to come through.
    Then it calls the handler function (from its own thread).
    """

    def __init__(self, socket_name: str) -> None:
        super().__init__(socket_name)

    def run(self) -> None:
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(self.socket_name)
                print(f"Opened preview stream socket {self.socket_name}")
            except (FileNotFoundError, ConnectionRefusedError):
                print("Socket does not exist yet, waiting...")
                time.sleep(2)
                continue

            while True:
                preview_len_b = self.sock.recv(4)
                if len(preview_len_b) != 4 or len(preview_len_b) > 10000000:
                    continue

                preview_len = int.from_bytes(preview_len_b, "big")

                preview_data = self.sock.recv(preview_len)

                if len(preview_data) != preview_len:
                    continue

                self.img_received.emit(preview_data)
                time.sleep(0.01)


class Photostrip:
    """
    A class for a photostrip made of several images and a background
    """
    photos: List[str]
    background: List[str]
    bg_width: int
    bg_height: int
    composited_im: Optional[IMAGE_T]

    def __init__(self, photos: List[str]):
        self.photo_list = photos
        self.bg_width = 0
        self.bg_height = 0
        self.composited_im = None

        self.background = boothconfig.BackgroundPath
        self.bg_mode = boothconfig.BackgroundMode

    def composite(self):
        """
        Add images to a background
        :return: Composite image
        """


        bg: IMAGE_T = Image.open(random.choice(self.background))
        photos = [Image.open(img) for img in self.photo_list]

        bg_w, bg_h = bg.size
        self.bg_width = bg_w
        self.bg_height = bg_h

        img_w, img_h = photos[0].size
        img_aspect = img_w/img_h

        if self.bg_mode == "SingleVerticaL":
            thumb_w = boothconfig.ThumbnailWidth
            thumb_h = int(thumb_w / img_aspect)

            left_offset = boothconfig.ThumbnailX
            top_offset = boothconfig.ThumbnailY
            skip_x = boothconfig.ThumbnailSkipX
            skip_y = boothconfig.ThumbnailSkipY

            for i, img in zip(range(len(photos)), photos):
                print(f"BG: {bg_w}x{bg_h}")
                print(f"Img: {img_w}x{img_h}")
                print(f"Thumb: {thumb_w}x{thumb_h}")

                p = img.copy()
                p.thumbnail((thumb_w, thumb_h))
                print(f"Thumb actual: {p.size}")

                vpos = top_offset + (thumb_h + skip_y) * i
                bg.paste(p, (left_offset, vpos))

        elif self.bg_mode == "DoubleVertical":
            print("Compositing on double background")
            thumb_w = boothconfig.ThumbnailWidth
            thumb_h = int(thumb_w / img_aspect)

            left_offset = boothconfig.ThumbnailX
            top_offset = boothconfig.ThumbnailY
            skip_x = boothconfig.ThumbnailSkipX
            skip_y = boothconfig.ThumbnailSkipY

            for i, img in zip(range(len(photos)), photos):
                print(f"BG: {bg_w}x{bg_h}")
                print(f"Img: {img_w}x{img_h}")
                print(f"Thumb: {thumb_w}x{thumb_h}")

                p = img.copy()
                p.thumbnail((thumb_w, thumb_h))
                print(f"Thumb actual: {p.size}")

                vpos = top_offset + (thumb_h + skip_y) * i
                bg.paste(p, (left_offset, vpos))
                bg.paste(p, (left_offset + skip_x + thumb_w, vpos))
        else:
            assert False, "Composite mode unknown"

        self.composited_im = bg
        return bg

    def width(self):
        return self.bg_width

    def height(self):
        return self.bg_height

    def make_printable(self):
        """
        Produce a 4x6 image
        :return: 4x6 image
        """
        t_margin, r_margin, b_margin, l_margin = boothconfig.Margins

        if not self.composited_im:
            self.composite()

        im = self.composited_im
        im_w, im_h = im.size

        if self.bg_mode == "SingleVertical":
            concat: IMAGE_T = Image.new("RGB", (im_w * 2 + l_margin + r_margin, im_h + t_margin + b_margin))
            concat.paste(im, (l_margin, t_margin))
            concat.paste(im, (im_w + l_margin, t_margin))
        elif self.bg_mode == "DoubleVertical":
            concat: IMAGE_T = Image.new("RGB", (im_w + l_margin + r_margin, im_h + t_margin + b_margin))
            concat.paste(im, (l_margin, t_margin))
        else:
            concat = im
        concat.save("output.jpg")
        return concat


class OneshotTimer(threading.Timer):
    """
    A simple countdown timer which runs in its own thread
    """
    def __init__(self, t: float):
        self.sem = threading.Semaphore(0)
        super().__init__(t, self._finish)
        self.start()

    def _finish(self):
        self.sem.release()

    def wait(self):
        self.sem.acquire(blocking=True)


class SequenceThread(threading.Thread):
    click_queue: queue.Queue

    def __init__(self, window, click_queue: queue.Queue, do_print: bool = False) -> None:
        super().__init__()
        self.window = window
        self.do_print = do_print
        self.click_queue = click_queue

    def _empty_click_queue(self):
        while True:
            # Empty the queue
            try:
                self.click_queue.get(block=False)
            except queue.Empty:
                break

    def run(self) -> None:
        if os.path.exists("capture.sock"):
            os.remove("capture.sock")
        capture_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        capture_socket.bind("capture.sock")
        capture_socket.settimeout(10)

        image_files: List[str] = []

        n_images = boothconfig.PhotosPerStrip
        countdown_len = boothconfig.CountdownTimer
        delay_between = boothconfig.DelayBetweenShots
        for i in range(n_images):
            topleft = f"{i+1}/{n_images}"
            for c in [str(x) for x in range(1, countdown_len + 1)[::-1]]:
                self.window.overlay.write(c, topleft)
                time.sleep(1)
            self.window.overlay.write("", topleft)

            print(f"Sending capture message")
            control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            control_socket.bind("")

            control_socket.sendto(b"cmd", "control.sock")

            timer_before_next = OneshotTimer(delay_between)

            try:
                data, addr = capture_socket.recvfrom(1048576)
                img_path = data.decode("UTF-8")
                print(f"Got image at {img_path}")
                image_files.append(img_path)
            except socket.timeout:
                pass

            self.window.overlay.write("", topleft)

            timer_before_next.wait()

            del control_socket

        self.window.overlay.write(topleft="Processing...")
        print(f"Photo set {image_files}")

        photostrip = Photostrip(image_files)
        img = photostrip.composite()
        # img.show()

        concat = photostrip.make_printable()
        concat.save("output.jpg")
        self.window.overlay.write()

        self._empty_click_queue()
        ncopies = 2
        timeleft = 20
        # self.window.overlay.write(f"- {int(timeleft):02d} +", f"Copies: {ncopies}")

        for i in range(200):
            timeleft = int(20.0 -  20 * float(i) / 200)
            self.window.overlay.write(f"- {ncopies} +", f"Copies?\n{timeleft:02d}s")
            try:
                x, y = self.click_queue.get(block=True, timeout=0.1)
            except queue.Empty:
                continue

            # Okay we have a click!
            print(f"Received {x},{y} click in sequence thread!")
            if 0.3 < y < 0.7:
                if x > 0.55:
                    ncopies = min(ncopies + 2, 6)
                elif x < 0.45:
                    ncopies = max(ncopies - 2, 0)
                elif 0.45 < x < 0.55:
                    self.window.overlay.write(f"", f"")
                    break

                self.window.overlay.write(f"- {ncopies} +", f"Copies?\n{timeleft:02d}s")

        if self.do_print:
            if ncopies > 0:
                self.window.overlay.write(f"", f"Printing...")
                for i in range(ncopies >> 1):
                    subprocess.run("lpr -P MITSUBISHI_CK60D70D707D output.jpg", shell=True)
            time.sleep(5)
        else:
            self.window.overlay.write(f"", f"NoPrint")
            time.sleep(5)

        self.window.overlay.write(f"", f"Tap to start")
        self.window.sequence_sem.release()


class QLabelClickable(QLabel):
    clicked = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

    def mousePressEvent(self, ev: QMouseEvent):
        pos = ev.localPos()

        self.clicked.emit(float(pos.x()) / self.width(), float(pos.y()) / self.height())


class OverlayText(QLabelClickable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._w = int(2000/1.4)
        self._h = int(1500/1.4)
        self._pixmap = QPixmap(self._w, self._h)
        self._pixmap.fill(Qt.transparent)
        # mask = self._pixmap.createMaskFromColor(Qt.black,Qt.MaskOutColor)
        self.painter = QPainter(self._pixmap)
        self.myparent = parent

        # Go initialize it
        self.write("")

    def write(self, text: str = "", topleft: str = "") -> None:

        self._pixmap.fill(Qt.transparent)
        self.painter.setBackgroundMode(Qt.TransparentMode)

        if text:
            self.painter.setPen(Qt.transparent)
            self.painter.setBrush(QBrush(QColor("#80c4ccff")))

            self.painter.drawEllipse(QPoint(int(self._w/2), int(self._h/2)), 150, 150)
            self.painter.setPen(Qt.black)
            # self.painter.setBrush(QBrush(Qt.green));
            self.painter.setFont(QFont("Consolas", pointSize=140))
            self.painter.drawText(QRect(0, 0, self._w, self._h), Qt.AlignCenter, text)

        if topleft:
            self.painter.setPen(Qt.black)
            # self.painter.setBrush(QBrush(Qt.green));
            self.painter.setFont(QFont("Consolas", pointSize=80))
            self.painter.drawText(QRect(0, 0, self._w, self._h), Qt.AlignTop | Qt.AlignLeft, topleft)

        #self.setPixmap(self._pixmap.scaled(self._w, self._h, Qt.KeepAspectRatio))
        self.setPixmap(self._pixmap)

    def resizeEvent(self, event):
        w = self.width()
        h = self.height()
        print(f"OverlayText resize event {w}x{h}")
        newpix = QPixmap(self._pixmap.scaled(w, h, Qt.KeepAspectRatio))
        self.old_pixmap = self._pixmap
        self.painter.end()
        self.painter = QPainter(newpix)
        self.setPixmap(newpix)
        self._pixmap = newpix
        self._w = w
        self._h = h


class FixedAspectRatioWidget(QWidget):
    def __init__(self, widget: QWidget, ratio: float, parent=None):
        super().__init__(parent)
        self.central_widget = widget
        self.aspect_ratio = ratio
        # widget.size().width() / widget.size().height()
        self.setLayout(QBoxLayout(QBoxLayout.LeftToRight, self))
        #  add spacer, then widget, then spacer
        spc_l = QSpacerItem(0, 0)
        spc_r = QSpacerItem(0, 0)
        spc_l = QWidget(self)
        spc_r = QWidget(self)

        self.layout().addWidget(spc_l)
        self.layout().addWidget(widget)
        self.layout().addWidget(spc_r)
        self.layout().setSpacing(0)

        #self.setStyleSheet("background-color:black;")
        spc_l.setStyleSheet("background-color:black;")
        spc_r.setStyleSheet("background-color:black;")

        self.also_resize = None

    def resizeEvent(self, e):
        w = e.size().width()
        h = e.size().height()

        print(f"FixedAR resize to {w}x{h}")

        if w / h > self.aspect_ratio:  # too wide
            self.layout().setDirection(QBoxLayout.LeftToRight)
            widget_stretch = h * self.aspect_ratio
            outer_stretch = (w - widget_stretch) / 2 + 0.5
        else:  # too tall
            self.layout().setDirection(QBoxLayout.TopToBottom)
            widget_stretch = w / self.aspect_ratio
            outer_stretch = (h - widget_stretch) / 2 + 0.5

        # No longer takes floats, so just add large multiplier so the proportions work out
        self.layout().setStretch(0, int(outer_stretch*1000))
        self.layout().setStretch(1, int(widget_stretch*1000))
        self.layout().setStretch(2, int(outer_stretch*1000))

        if self.also_resize:
            self.also_resize.resize(self.central_widget.size())


class CameraControlWindow(QMainWindow):
    click_queue: queue.Queue

    def __init__(self, parent=None, do_print: bool = False, bg_mode: str = "Single"):
        super(CameraControlWindow, self).__init__(parent)
        self.setWindowTitle("Photobooth GUI")
        self.do_print = do_print
        self.bg_mode = bg_mode

        # Set background black
        p = self.palette()
        p.setColor(self.backgroundRole(), Qt.black)
        self.setPalette(p)

        # Create a widget for window contents
        im_plus_overlay = QWidget(self)

        # Wrap it in a thing that will force a fixed aspect ratio
        wid = FixedAspectRatioWidget(im_plus_overlay, 1.5, self)
        self.setCentralWidget(wid)
        wid.layout().setContentsMargins(0, 0, 0, 0)

        # Create exit action
        exitAction = QAction(QIcon('exit.png'), '&Exit', wid)
        exitAction.setShortcut('Ctrl+Q')
        exitAction.setStatusTip('Exit application')
        exitAction.triggered.connect(self.exitCall)

        self.click_queue = queue.Queue()
        self.imageWidget = QLabelClickable(wid)
        self.imageWidget.clicked.connect(self.handleClick)
        self.imageWidget.setScaledContents(True)

        self.overlay = OverlayText(wid)
        self.overlay.clicked.connect(self.handleClick)
        #self.overlay.setScaledContents(True)
        wid.also_resize = self.overlay

        layout = QGridLayout()
        layout.addWidget(self.imageWidget, 0, 0)
        layout.addWidget(self.overlay, 0, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        # Remove extra spacing on the sides
        layout.setContentsMargins(0, 0, 0, 0)

        # Set widget to contain window contents
        im_plus_overlay.setLayout(layout)

        self.sequence_thread: Optional[SequenceThread] = None
        self.sequence_sem = threading.Semaphore(1)

        # Launch the RX thread
        self.receiver = ImageReceiverStream("preview.sock")
        self.rx_thread = QThread(self)

        self.receiver.img_received.connect(self.handlePreview)
        self.receiver.moveToThread(self.rx_thread)
        self.rx_thread.started.connect(self.receiver.run)
        self.rx_thread.start()

    @pyqtSlot(object)
    def handlePreview(self, image):
        """ Callback to take an image (pile o' bytes) and update the display
        """
        q = QImage()
        q.loadFromData(image)
        self.imageWidget.setPixmap(QPixmap(q))

    def handleClick(self, x: float, y: float):
        print(f"Click location {x}, {y}")

        if self.sequence_sem.acquire(blocking=False):

            self.sequence_thread = SequenceThread(self, click_queue=self.click_queue, do_print=self.do_print)
            self.sequence_thread.start()
        else:
            self.click_queue.put((x, y))

    @staticmethod
    def exitCall():
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Launch the BugBooth GUI")
    parser.add_argument("--do_print", action="store_true", help="Actually print the compilations")
    parser.add_argument("--fs", action="store_true", help="Display in full screen")
    parser.add_argument("--config_file", type=str, default="bugbooth.conf")

    args = parser.parse_args()

    boothconfig = BugBoothConfig(args.config_file)
    _app = QApplication(sys.argv)
    _window = CameraControlWindow(do_print=args.do_print)
    if args.fs:

        # Try to resize to the screen size
        screen = _app.desktop()
        res = screen.availableGeometry()
        _window.resize(res.size())
        _window.showFullScreen()
    else:
        _window.show()
    sys.exit(_app.exec_())
