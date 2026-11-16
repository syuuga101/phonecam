import os, subprocess, sys
import pytest
pytest.importorskip("PySide6.QtWidgets")

def run(args):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-x", "-s", "-p", "no:cacheprovider", *args], capture_output=True, env=env,
                       cwd=os.path.dirname(os.path.dirname(__file__)), timeout=170)
    return "rc=%s\n%s\n----\n%s" % (r.returncode, r.stdout.decode("utf-8", "replace")[-2500:], r.stderr.decode("utf-8", "replace")[-2500:])

def test_dbg_alone():
    assert False, "ALONE " + run(["tests/test_ready_qt.py"])

def test_dbg_after_qt():
    assert False, "AFTERQT " + run(["tests/test_qt.py", "tests/test_ready_qt.py"])
