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


import subprocess
import sys
import argparse
import time

if __name__ != "__main__":
    sys.exit(1)


def kill_old_servers():
    for grep in ["camera_server.py", "./photobooth_gui.py"]:
        completed_ps_process = subprocess.run(f"ps aux|grep python|grep {grep}", stdout=subprocess.PIPE, shell=True)
        lines = completed_ps_process.stdout.decode("UTF-8").split("\n")
        for l in lines[:-2]:
            cols = [x for x in l.split(" ") if x]
            print(cols)
            subprocess.run(f"kill {cols[1]}", stdout=subprocess.PIPE, shell=True)


kill_old_servers()

parser = argparse.ArgumentParser(description="Launch the BugBooth photobooth")
parser.add_argument("--do_print", action="store_true", help="Actually print the compilations")
parser.add_argument("--fs", action="store_true", help="Display in full screen")
parser.add_argument("--mock", action="store_true", help="Use fake input data")
parser.add_argument("--nimages", type=int, default=4, help="Number of images in a strip")

args = parser.parse_args()

print(f"FS: {args.fs}")
print(f"Print: {args.do_print}")

server_cmd = "./camera_server.py"
if args.mock:
    server_cmd += " --mock"
server_proc = subprocess.Popen(server_cmd, shell=True)

time.sleep(1)
gui_cmd = f"./photobooth_gui.py --nimages {args.nimages}"
if args.fs:
    gui_cmd += " --fs"
if args.do_print:
    gui_cmd += " --do_print"
gui_proc = subprocess.Popen(gui_cmd, shell=True)

try:
    gui_proc.wait()
    print("GUI completed")
    server_proc.kill()
    print("Server killed")
    server_proc.wait()
finally:
    kill_old_servers()
