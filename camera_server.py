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

""" A simple tool to pull a stream of preview frames and, upon request, take a picture
"""
import sys
import os
import socket
import struct
import threading
import time
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from typing import Union, Optional, List, Dict, Tuple, Any, Callable

import gphoto2 as gp


class PBCamera:
    """ An object which creates two sockets:
    1. A datagram control socket which takes commands to take photos and then spits the photo back
    out
    2. A streaming socket which constantly outputs an MJPEG preview from the camera
    """
    def __init__(self, control_sock_name="control.sock", capture_sock_name="capture.sock", mock: bool=False) -> None:
        self.preview_thread = None

        self.control_sock_name = control_sock_name
        self.control_sock = None
        self.control_thread = None

        self.capture_sock_name = capture_sock_name

        self.io_lock = threading.Lock()

        self.camera_config = None
        self.camera: Optional[Any] = None
        self.old_capturetarget = None
        self.camera_model = ""

        self.mock = mock

    def _open_camera(self):
        if self.mock:
            # I guess just do nothing...
            pass
        else:
            while True:
                try:
                    self.camera = gp.Camera()
                    self.camera.init()
                    break
                except gp.GPhoto2Error:
                    print("*** Error opening camera; retrying")
                    time.sleep(1)
            self.camera_config = self.camera.get_config()
            # get the camera model
            OK, camera_model = gp.gp_widget_get_child_by_name(
                self.camera_config, 'cameramodel')
            if OK < gp.GP_OK:
                OK, camera_model = gp.gp_widget_get_child_by_name(
                    self.camera_config, 'model')
            if OK >= gp.GP_OK:
                self.camera_model = camera_model.get_value()
                print('Camera model:', self.camera_model)
            else:
                print('No camera model info')

            OK, capture_target = gp.gp_widget_get_child_by_name(
                self.camera_config, 'capturetarget')

            if OK >= gp.GP_OK:
                if self.old_capturetarget is None:
                    self.old_capturetarget = capture_target.get_value()

    def open(self) -> None:
        """ Open the preview and control sockets
        """
        try:
            os.unlink(self.control_sock_name)
        except OSError:
            if os.path.exists(self.control_sock_name):
                raise

        self.control_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.control_sock.bind(self.control_sock_name)

        self._open_camera()

    def close(self) -> None:
        """ Close all sockets and shut down
        """
        pass

    def _capture_image(self) -> None:
        with self.io_lock:
            if self.mock:
                self.control_sock.sendto("mock_image.jpg".encode("UTF-8"), self.capture_sock_name)
            else:
                print("Taking image")
                while True:
                    try:
                        file_path = gp.check_result(gp.gp_camera_capture(self.camera, gp.GP_CAPTURE_IMAGE))
                        break
                    except gp.GPhoto2Error:
                        print("*** Error captuing; retrying")
                        self._open_camera()
                        time.sleep(0.1)

                print("Took image")
                print('Camera file path: {0}/{1}'.format(file_path.folder, file_path.name))
                target = os.path.join('/tmp', file_path.name)
                print('Copying image to', target)
                camera_file = gp.check_result(gp.gp_camera_file_get(self.camera, file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL))

                target = tempfile.mktemp(suffix=".jpg", dir=".")
                if os.path.exists(target):
                    os.remove(target)
                gp.check_result(gp.gp_file_save(camera_file, target))

                try:
                    self.control_sock.sendto(target.encode("UTF-8"), self.capture_sock_name)
                except ConnectionRefusedError:
                    print("Connection was refused")
                self._open_camera()

    def _capture_preview(self) -> bytes:
        with self.io_lock:
            if self.mock:
                file_bytes = open("mock_preview.jpg", "rb").read()
                time.sleep(0.05)
            else:
                time.sleep(0.05)
                while True:
                    try:
                        camera_file = gp.check_result(gp.gp_camera_capture_preview(self.camera))
                        break
                    except gp.GPhoto2Error:
                        print("*** Error captuing preview; retrying")
                        time.sleep(0.05)
                file_data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
                file_bytes = memoryview(file_data)
        return file_bytes

    def _preview_thread_action(self) -> None:
        return

    def _control_thread_action(self) -> None:
        while True:
            data, addr = self.control_sock.recvfrom(1048576)
            if data:
                self._capture_image()
            time.sleep(0.01)

    def run(self) -> None:
        """ Begin previewing and listening for commands
        """
        self.preview_thread = threading.Thread(target=self._preview_thread_action)
        self.control_thread = threading.Thread(target=self._control_thread_action)

        self.preview_thread.start()
        self.control_thread.start()

        # Thing runs

        self.preview_thread.join()
        self.control_thread.join()

class HTTPPBCamera(PBCamera):
    def __init__(self, mock: bool=False) -> None:
        super().__init__(mock=mock)

    def _preview_thread_action(self) -> None:
        """ Send data via an HTTP server
        """
        camera_self = self


        class PreviewHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-type','multipart/x-mixed-replace; boundary=--jpgboundary')
                self.end_headers()
                while True:
                    try:
                        preview = camera_self._capture_preview()
                        self.wfile.write(b"--jpgboundary")
                        self.send_header('Content-type','image/jpeg')
                        #self.send_header('Content-length',str(len(preview)))
                        self.end_headers()
                        self.wfile.write(preview)
                        time.sleep(0.05)
                    except KeyboardInterrupt:
                        break
                    except ConnectionResetError:
                        break
                return
        server = HTTPServer(('localhost', 8080), PreviewHandler)
        server.serve_forever()
        return

class DomainStreamPBCamera(PBCamera):
    def __init__(self, preview_file:str="./preview.sock", mock: bool=False) -> None:
        self.preview_file = preview_file
        self.preview_socket:Optional[socket.socket] = None
        super().__init__(mock)

    def _preview_thread_action(self) -> None:
        """ Send data as MJPEG stream
        """
        # Make sure the socket does not already exist
        try:
            os.unlink(self.preview_file)
        except OSError:
            if os.path.exists(self.preview_file):
                raise

        # Open the socket for video preview
        self.preview_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.preview_socket.bind(self.preview_file)

        self.preview_socket.listen(1)
        while True:
            connection, client_address = self.preview_socket.accept()
            print("Got connection")
            while True:
                with self.io_lock:
                    camera_file = gp.check_result(gp.gp_camera_capture_preview(self.camera))
                    file_data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
                    file_bytes = bytes(memoryview(file_data))
                try:
                    connection.sendall(file_bytes)
                except BrokenPipeError:
                    print("Connection closed")
                    break


class DomainDGramPBCamera(PBCamera):
    def __init__(self, preview_file: str = "./preview.sock", mock: bool = False) -> None:
        self.preview_socket: Optional[socket.socket] = None
        self.preview_file: str = preview_file
        super().__init__(mock=mock)

    def _preview_thread_action(self) -> None:
        """ Send data as a sequence of JPEG datagrams
        """
        self.preview_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.preview_socket.bind("")
        failcount = 0
        while True:
            image = self._capture_preview()
            try:
                self.preview_socket.sendto(image, self.preview_file)
            except ConnectionRefusedError:
                pass
            except FileNotFoundError:
                print(f"Failed to write to socket {self.preview_file}")
                if os.path.exists(self.preview_file):
                    print("It exists though...")
                else:
                    print("It really doesn't exist")
                if failcount == 2:
                    sys.exit(1)
                failcount += 1
            time.sleep(0.05)


def exec_server(mock: bool) -> None:
    """ Run the server
    """
    camera = DomainDGramPBCamera(mock=mock)
    camera.open()
    camera.run()


if __name__ == "__main__":
    mock = False
    if "--mock" in sys.argv:
        mock = True

    exec_server(mock)
    sys.exit(0)
