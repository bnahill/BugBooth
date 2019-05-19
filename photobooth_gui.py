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
import os

from PIL import Image

from typing import Union, Optional, List, Dict, Tuple, Callable

from PyQt5.QtCore import QDir, Qt, QUrl, QIODevice, pyqtSignal, QPoint, QRect, QObject, pyqtSlot, pyqtSignal, QThread
from PyQt5.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
        QPushButton, QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)
from PyQt5.QtWidgets import QMainWindow,QWidget, QPushButton, QAction, QGridLayout
from PyQt5.QtGui import QIcon, QImage, QPixmap, QPainter, QFont, QBitmap, QBrush, QPen, QColor


class ImageReceiver(QObject):
    """ Listens to a domain socket waiting for images to come through.
    Then it calls the handler function (from its own thread).
    """

    img_received = pyqtSignal(object)

    def __init__(self, socket_name: str) -> None:
        super().__init__()
        self.socket_name = socket_name
        self.sock: Optional[socket.socket] = None

    def run(self) -> None:
        try:
            os.unlink(self.socket_name)
        except OSError:
            if os.path.exists(self.socket_name):
                raise

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.socket_name)
        print(f"Opened preview socket {self.socket_name}")

        while True:
            data, addr = self.sock.recvfrom(1048576)
            if data:
                self.img_received.emit(data)
            time.sleep(0.01)


class CompositeImage:
    def __init__(self, photos: List[str], background: str):
        self.photo_list = photos
        self.background = background

    def composite(self):
        bg:Image = Image.open(self.background)
        photos = [Image.open(img) for img in self.photo_list]

        bg_w, bg_h = bg.size

        img_w, img_h = photos[0].size
        img_aspect = img_w/img_h

        thumb_w = int(bg_w * 0.95)
        thumb_h = int(thumb_w / img_aspect)

        thumb_offset = int((bg_w - thumb_w) / 2)

        for i, img in zip(range(len(photos)), photos):
            print(f"BG: {bg_w}x{bg_h}")
            print(f"Img: {img_w}x{img_h}")
            print(f"Thumb: {thumb_w}x{thumb_h}")

            p = img.copy()
            p.thumbnail((thumb_w, thumb_h))
            print(f"Thumb actual: {p.size}")

            bg.paste(p, (thumb_offset, thumb_offset + int(1.2*thumb_h) * i))
        return bg


class SequenceThread(threading.Thread):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window

    def run(self) -> None:
        if os.path.exists("capture.sock"):
            os.remove("capture.sock")
        capture_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        capture_socket.bind("capture.sock")
        capture_socket.settimeout(10)

        image_files: List[str] = []

        n_images = 3
        for i in range(n_images):
            topleft = f"{i+1}/{n_images}"
            for c in "321":
                self.window.overlay.write(c, topleft)
                time.sleep(1)
            self.window.overlay.write("", topleft)
            print(f"Sending capture message")
            control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            control_socket.bind("")

            control_socket.sendto(b"cmd", "control.sock")

            data, addr = capture_socket.recvfrom(1048576)
            img_path = data.decode("UTF-8")
            print(f"Got image at {img_path}")
            image_files.append(img_path)
            self.window.overlay.write("", topleft)

            time.sleep(3)

            del control_socket
            self.window.sequence_sem.release()
        self.window.overlay.write()
        print(f"Photo set {image_files}")

        print("USING ABSOLUTE PATH FOR BACKGROUND")
        bg_file = "/home/ben/Downloads/Background_1_color1.png"
        c = CompositeImage(image_files, bg_file)
        img = c.composite()
        img.show()
        del capture_socket


class QLabelClickable(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

    def mousePressEvent(self, ev):
        self.clicked.emit()


class OverlayText(QLabelClickable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._w = int(3000/1.8)
        self._h = int(2000/1.8)
        self.pixmap = QPixmap(self._w, self._h)
        self.pixmap.fill(Qt.transparent)
        # mask = self.pixmap.createMaskFromColor(Qt.black,Qt.MaskOutColor)
        self.painter = QPainter(self.pixmap)
        self.myparent = parent

        # Go initialize it
        self.write("")

    def write(self, text: str = "", topleft: str = "") -> None:
        self.pixmap.fill(Qt.transparent)
        self.painter.setBackgroundMode(Qt.TransparentMode)

        if text:
            self.painter.setPen(Qt.transparent)
            self.painter.setBrush(QBrush(QColor("#80c4ccff")))

            self.painter.drawEllipse(QPoint(int(self._w/2), int(self._h/2)), 150, 150)
            self.painter.setPen(Qt.black)
            # self.painter.setBrush(QBrush(Qt.green));
            self.painter.setFont(QFont("Arial", pointSize=140))
            self.painter.drawText(QRect(0, 0, self.width(), self.height()), Qt.AlignCenter, text)

        if topleft:
            self.painter.setPen(Qt.black)
            # self.painter.setBrush(QBrush(Qt.green));
            self.painter.setFont(QFont("Arial", pointSize=100))
            self.painter.drawText(QRect(0, 0, self.width(), self.height()), Qt.AlignTop | Qt.AlignLeft, topleft)

        self.setPixmap(self.pixmap)

    def resizeEvent(self, event):
        w = self.width()
        h = self.height()
        print(f"Resize event {w}x{h}")
        newpix = self.pixmap.scaled(self.width(), self.height(), Qt.KeepAspectRatio)
        self.setPixmap(newpix)


class CameraControlWindow(QMainWindow):
    def __init__(self, parent=None):
        super(CameraControlWindow, self).__init__(parent)
        self.setWindowTitle("Photobooth GUI")

        # Create a widget for window contents
        wid = QWidget(self)
        self.setCentralWidget(wid)

        # Create exit action
        exitAction = QAction(QIcon('exit.png'), '&Exit', wid)
        exitAction.setShortcut('Ctrl+Q')
        exitAction.setStatusTip('Exit application')
        exitAction.triggered.connect(self.exitCall)

        self.imageWidget = QLabelClickable(wid)
        self.imageWidget.clicked.connect(self.handleClick)
        self.imageWidget.setScaledContents(True)

        self.overlay = OverlayText(wid)
        self.overlay.clicked.connect(self.handleClick)
        self.overlay.setScaledContents(True)

        layout = QGridLayout()
        layout.addWidget(self.imageWidget, 0, 0)
        layout.addWidget(self.overlay, 0, 0, Qt.AlignHCenter|Qt.AlignVCenter)

        # Set widget to contain window contents
        wid.setLayout(layout)

        self.sequence_thread: Optional[SequenceThread] = None
        self.sequence_sem = threading.Semaphore(1)

        # Launch the RX thread
        self.receiver = ImageReceiver("preview.sock")
        self.rx_thread = QThread(self)

        self.receiver.img_received.connect(self.handlePreview)
        self.receiver.moveToThread(self.rx_thread)
        self.rx_thread.started.connect(self.receiver.run)
        self.rx_thread.start()
        print("CONSTRUCTED")

    @pyqtSlot(object)
    def handlePreview(self, image):
        """ Callback to take an image (pile o' bytes) and update the display
        """
        q = QImage()
        q.loadFromData(image)
        self.imageWidget.setPixmap(QPixmap(q))

    def handleClick(self):
        if self.sequence_sem.acquire(blocking=False):
            self.sequence_thread = SequenceThread(self)
            self.sequence_thread.start()
        # control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # control_socket.bind("")
        # control_socket.sendto(b"cmd", "control.sock")

    def exitCall(self):
        sys.exit(1)


if __name__ == '__main__':
    fs = False
    if "--fs" in sys.argv:
        fs = True
    _app = QApplication(sys.argv)
    _window = CameraControlWindow()
    if fs:
        _window.showFullScreen()
    else:
        _window.show()
    sys.exit(_app.exec_())
