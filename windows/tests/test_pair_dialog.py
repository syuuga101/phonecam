import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from phonecam.i18n import t
from phonecam.qt.wireless import CodeDialog


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


class FakeSession:
    instances = []

    def __init__(self, address, identity, on_state):
        self.address, self.identity, self.on_state = address, identity, on_state
        self.closed, self.submitted, self.result = False, None, None
        FakeSession.instances.append(self)

    def submit(self, code):
        self.submitted = code

    def close(self):
        self.closed = True


def make(app, address="192.168.1.7"):
    FakeSession.instances.clear()
    dialog = CodeDialog(None, "iPhone", address, identity=object(), session=FakeSession)
    return dialog, (FakeSession.instances[0] if FakeSession.instances else None)


def test_no_address_starts_no_session(app):
    dialog, session = make(app, None)
    assert session is None and dialog.note.text() == t("wireless.code_note")
    dialog.deleteLater()


def test_states_change_the_note_and_close_cancels_session(app):
    dialog, session = make(app)
    assert session.address == "192.168.1.7"
    for state, key in (("waiting", "wireless.code_note_wait"), ("shown", "wireless.code_note_popup"),
                       ("background", "wireless.code_note_background"), ("code", "wireless.code_note")):
        session.on_state(state)
        dialog._poll()
        assert dialog.note.text() == t(key)
    dialog.reject()
    assert session.closed and not dialog.declined
    dialog.deleteLater()


def test_declined_closes_dialog_and_reports(app):
    dialog, session = make(app)
    dialog.show()
    session.on_state("declined")
    dialog._poll()
    assert dialog.declined and not dialog.isVisible() and session.closed
    dialog.deleteLater()


def test_entering_code_submits_it_and_waits_for_the_result(app):
    dialog, session = make(app)
    dialog.edit.setText("123456")
    dialog.edit.textEdited.emit("123456")
    dialog._accept()
    assert dialog.code == "123456" and session.submitted == "123456" and not session.closed
    assert dialog.note.text() == t("wireless.code_checking")
    session.result = {"s": b"x"}
    session.on_state("paired")
    dialog._poll()
    assert dialog.peer == {"s": b"x"} and session.closed
    dialog.deleteLater()


def test_wrong_code_is_final(app):
    dialog, session = make(app)
    dialog.edit.setText("123456")
    dialog.edit.textEdited.emit("123456")
    dialog._accept()
    session.on_state("mismatch")
    dialog._poll()
    assert dialog.note.text() == t("wireless.code_wrong") and not dialog.edit.isEnabled()
    dialog.deleteLater()
