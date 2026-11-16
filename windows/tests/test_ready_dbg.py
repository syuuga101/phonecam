import os, subprocess, sys
import pytest
pytest.importorskip("PySide6.QtWidgets")

CODE = r'''
import sys, faulthandler
faulthandler.enable()
from PySide6.QtWidgets import QApplication
app = QApplication([])
from phonecam.controller import Controller
from phonecam.core.ready import READY, UsbSeen
from phonecam.core.wireless import Device
from phonecam.qt.main_window import NAV_ITEMS, MainWindow
KNOWN = Device("5f1c0c1e-9a53-4a0e-8b8e-0b6f2f1d3a11", "iPhone 13", ("192.168.1.81",), "", "iPhone 13", "", "11" * 32)
print("A", flush=True)
ctrl = Controller(persist=False, demo=True)
ctrl.settings["known_devices"] = [KNOWN.stored()]
ctrl.demo_usb_seen = {"S": UsbSeen(READY, KNOWN.id)}
ctrl.poll()
print("B", flush=True)
win = MainWindow(ctrl, tick=False)
win.resize(1280, 800)
print("C", flush=True)
win.show()
print("D", flush=True)
win.select(next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "connection"))
print("E", flush=True)
app.processEvents()
print("F", win.page_errors, flush=True)
'''

def test_dbg():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", CODE], capture_output=True, text=True, env=env, cwd=os.path.dirname(os.path.dirname(__file__)))
    print(r.stdout[-3000:]); print(r.stderr[-6000:])
    assert False, "rc=%s " % r.returncode + r.stdout[-1500:] + "\n----\n" + r.stderr[-4000:]
