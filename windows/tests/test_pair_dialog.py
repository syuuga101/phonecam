import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from phonecam.core import pairreq
from phonecam.i18n import t
from phonecam.qt.wireless import CodeDialog


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


class FakeRequester:
    instances = []

    def __init__(self, address, name, on_state):
        self.address, self.name, self.on_state = address, name, on_state
        self.closed = False
        FakeRequester.instances.append(self)

    def start(self):
        return self

    def close(self):
        self.closed = True


def make(app, address="192.168.1.7"):
    FakeRequester.instances.clear()
    dialog = CodeDialog(None, "iPhone", address, requester=FakeRequester)
    return dialog, (FakeRequester.instances[0] if FakeRequester.instances else None)


def test_no_address_sends_no_request(app):
    dialog, requester = make(app, None)
    assert requester is None
    assert dialog.note.text() == t("wireless.code_note")
    dialog.deleteLater()


def test_states_change_the_note_and_close_cancels_request(app):
    dialog, requester = make(app)
    assert requester.address == "192.168.1.7"
    requester.on_state(pairreq.SHOWN)
    dialog._poll()
    assert dialog.note.text() == t("wireless.code_note_popup")
    requester.on_state(pairreq.BACKGROUND)
    dialog._poll()
    assert dialog.note.text() == t("wireless.code_note_background")
    requester.on_state(pairreq.FALLBACK)
    dialog._poll()
    assert dialog.note.text() == t("wireless.code_note")
    dialog.reject()
    assert requester.closed and not dialog.declined
    dialog.deleteLater()


def test_declined_closes_dialog_and_reports(app):
    dialog, requester = make(app)
    dialog.show()
    requester.on_state(pairreq.DECLINED)
    dialog._poll()
    assert dialog.declined and not dialog.isVisible() and requester.closed
    dialog.deleteLater()


def test_entering_code_still_works_with_popup(app):
    dialog, requester = make(app)
    requester.on_state(pairreq.SHOWN)
    dialog._poll()
    dialog.edit.setText("123456")
    dialog.edit.textEdited.emit("123456")
    dialog._accept()
    assert dialog.code == "123456" and requester.closed
    dialog.deleteLater()
