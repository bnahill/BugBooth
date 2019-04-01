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
from http.server import BaseHTTPRequestHandler, HTTPServer

import gphoto2 as gp


class PBCamera:
    """ An object which creates two sockets:
    1. A datagram control socket which takes commands to take photos and then spits the photo back
    out
    2. A streaming socket which constantly outputs an MJPEG preview from the camera
    """
    def __init__(self):
        self.preview_socket = None
        self.preview_thread = None

        self.control_socket = None
        self.control_thread = None

        self.io_lock = threading.Lock()

    def open(self, preview_file="./preview.sock"):
        """ Open the preview and control sockets
        """
        # Make sure the socket does not already exist
        try:
            os.unlink(preview_file)
        except OSError:
            if os.path.exists(preview_file):
                raise

        self.camera = gp.Camera()
        self.camera.init()
        camera_config = self.camera.get_config()
        # get the camera model
        OK, camera_model = gp.gp_widget_get_child_by_name(
            camera_config, 'cameramodel')
        if OK < gp.GP_OK:
            OK, camera_model = gp.gp_widget_get_child_by_name(
                camera_config, 'model')
        if OK >= gp.GP_OK:
            self.camera_model = camera_model.get_value()
            print('Camera model:', self.camera_model)
        else:
            print('No camera model info')

        # Open the socket for video preview
        self.preview_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.preview_socket.bind(preview_file)

    def close(self):
        """ Close all sockets and shut down
        """
        pass

    def _capture_image(self):
        with self.io_lock:
            print("Taking image")
            file_path = gp.check_result(gp.gp_camera_capture(self.camera, gp.GP_CAPTURE_IMAGE))
            print("Took image")
            print('Camera file path: {0}/{1}'.format(file_path.folder, file_path.name))
            target = os.path.join('/tmp', file_path.name)
            print('Copying image to', target)
            camera_file = gp.check_result(gp.gp_camera_file_get(self.camera, file_path.folder, file_path.name, gp.GP_FILE_TYPE_NORMAL))
            gp.check_result(gp.gp_file_save(camera_file, target))

    def _capture_preview(self):
        with self.io_lock:
            camera_file = gp.check_result(gp.gp_camera_capture_preview(self.camera))
            file_data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
            file_bytes = memoryview(file_data)
        return file_bytes

    def _preview_thread_action_http_mjpeg(self):
        """ Send data via an HTTP server. Not used currently
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

    def _preview_thread_action_mjpeg_stream(self):
        """ Send data as MJPEG stream. Not used currently
        """
        self.preview_socket.listen(1)
        while True:
            connection, client_address = self.preview_socket.accept()
            print("Got connection")
            while True:
                with self.io_lock:
                    camera_file = gp.check_result(gp.gp_camera_capture_preview(self.camera))
                    file_data = gp.check_result(gp.gp_file_get_data_and_size(camera_file))
                    file_bytes = memoryview(file_data)
                try:
                    connection.sendall(file_bytes)
                except BrokenPipeError:
                    print("Connection closed")
                    break

    def _preview_thread_action(self):
        """ Send data as a sequence of JPEG datagrams
        """
        self.out_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.out_socket.bind("")
        while True:
            image = self._capture_preview()
            try:
                self.out_socket.sendto(image, "preview_dgram.sock")
            except ConnectionRefusedError:
                pass
            time.sleep(0.05)

    def _control_thread_action(self):
        while True:
            time.sleep(10)
            #self._capture_image()

    def run(self):
        """ Begin previewing and listening for commands
        """
        self.preview_thread = threading.Thread(target=self._preview_thread_action)
        self.control_thread = threading.Thread(target=self._control_thread_action)

        self.preview_thread.start()
        self.control_thread.start()

        # Thing runs

        self.preview_thread.join()
        self.control_thread.join()

def exec_server():
    """ Run the server
    """
    camera = PBCamera()
    camera.open()
    camera.run()

if __name__ == "__main__":
    exec_server()
    sys.exit(0)

#int
#action_camera_capture_movie (GPParams *p, const char *arg)
#{
#	CameraFile	*file;
#	int		r;
#	int		fd;
#	time_t		st;
#	enum moviemode	mm;
#	int		frames,captured_frames=0;
#	char		*xname;
#	struct timeval	starttime;
#
#	if (p->flags & FLAGS_STDOUT) {
#		fd = dup(fileno(stdout));
#		xname = "stdout";
#	} else {
#		fd = open("movie.mjpg",O_WRONLY|O_CREAT,0660);
#		if (fd == -1) {
#			cli_error_print(_("Could not open 'movie.mjpg'."));
#			return GP_ERROR;
#		}
#		xname = "movie.mjpg";
#	}
#	if (!arg) {
#		mm = MOVIE_ENDLESS;
#		fprintf(stderr,_("Capturing preview frames as movie to '%s'. Press Ctrl-C to abort.\n"), xname);
#	} else {
#		if (strchr(arg,'s')) {
#			sscanf (arg, "%ds", &frames);
#			fprintf(stderr,_("Capturing preview frames as movie to '%s' for %d seconds.\n"), xname, frames);
#			mm = MOVIE_SECONDS;
#			time (&st);
#		} else {
#			sscanf (arg, "%d", &frames);
#			fprintf(stderr,_("Capturing %d preview frames as movie to '%s'.\n"), frames, xname);
#			mm = MOVIE_FRAMES;
#		}
#	}
#	CR (gp_file_new_from_fd (&file, fd));
#	gettimeofday (&starttime, NULL);
#	while (1) {
#		const char *mime;
#		r = gp_camera_capture_preview (p->camera, file, p->context);
#		if (r < 0) {
#			cli_error_print(_("Movie capture error... Exiting."));
#			break;
#		}
#		gp_file_get_mime_type (file, &mime);
#                if (strcmp (mime, GP_MIME_JPEG)) {
#			cli_error_print(_("Movie capture error... Unhandled MIME type '%s'."), mime);
#			break;
#		}
#
#		captured_frames++;
#
#		if (glob_cancel) {
#			fprintf(stderr, _("Ctrl-C pressed ... Exiting.\n"));
#			break;
#		}
#		if (mm == MOVIE_FRAMES) {
#			if (!frames--)
#				break;
#		}
#		if (mm == MOVIE_SECONDS) {
#			if ((-timediff_now (&starttime)) >= frames*1000)
#				break;
#		}
#	}
#	gp_file_unref (file);
#
#	fprintf(stderr,_("Movie capture finished (%d frames)\n"), captured_frames);
#	return GP_OK;
#}
