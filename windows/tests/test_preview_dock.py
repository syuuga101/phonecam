import pytest

pytest.importorskip("PySide6.QtWidgets")
np = pytest.importorskip("numpy")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from phonecam.controller import Controller  # noqa: E402
from phonecam.qt import preview_dock  # noqa: E402
from phonecam.qt.main_window import NAV_ITEMS, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


def row_of(key):
    return next(i for i, (k, _icon) in enumerate(NAV_ITEMS) if k == key)


@pytest.fixture
def window(app):
    ctrl = Controller(persist=False, demo=True)
    win = MainWindow(ctrl, tick=False)
    win.resize(1300, 800)
    win.show()
    app.processEvents()
    yield win
    win.close()
    win.deleteLater()
    app.processEvents()


def test_dock_shows_only_on_target_pages_and_toggle_is_saved(app, window):
    win = window
    for key in ("video", "quality", "effects", "audio"):
        win.select(row_of(key))
        app.processEvents()
        assert win.dock.isVisible(), key
        assert win.dock_toggle.isVisible(), key
    win.select(row_of("connection"))
    app.processEvents()
    assert not win.dock.isVisible()
    assert not win.dock_toggle.isVisible()
    win.select(row_of("video"))
    win.dock_toggle.switch.setChecked(False)
    app.processEvents()
    assert not win.dock.isVisible()
    assert win.ctrl.get("preview_dock") is False
    win.select(row_of("audio"))      # 全ページ共通: 別のページでも隠れたまま
    app.processEvents()
    assert not win.dock.isVisible()
    win.dock_toggle.switch.setChecked(True)
    app.processEvents()
    assert win.dock.isVisible()
    assert win.ctrl.get("preview_dock") is True


def test_dock_moves_between_side_column_and_top_band(app, window):
    win = window
    win.select(row_of("video"))
    app.processEvents()
    assert win._body.indexOf(win.dock) >= 0
    assert win.dock.surface.width() == preview_dock.WIDE_PREVIEW_W
    win.resize(preview_dock.WIDE_W, 700)
    app.processEvents()
    assert win._body.indexOf(win.dock) >= 0
    assert win.current_page().horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    win.resize(preview_dock.WIDE_W - 1, 700)
    app.processEvents()
    assert win._pagecol.indexOf(win.dock) >= 0
    assert win._body.indexOf(win.dock) < 0
    assert win.dock.height() == preview_dock.BAND_H
    assert win.dock.surface.height() * 16 // 9 == win.dock.surface.width()
    assert win.dock.isVisible()
    win.resize(1300, 800)
    app.processEvents()
    assert win._body.indexOf(win.dock) >= 0


def test_audio_page_shows_level_and_mute_only_there(app, window):
    win = window
    win.select(row_of("video"))
    app.processEvents()
    assert not win.dock.audio_row.isVisible()
    win.select(row_of("audio"))
    app.processEvents()
    assert win.dock.audio_row.isVisible()
    win.ctrl.changed("mic_enabled", True)
    win.ctrl.changed("mic_mute", True)
    win.dock._refresh_info()
    assert win.dock.mic_label.text() == preview_dock.t("compact.sum.mic.muted")


def test_dock_does_not_draw_while_hidden(app, window, monkeypatch):
    win = window
    calls = []
    monkeypatch.setattr(preview_dock, "frame_to_image", lambda *a, **k: calls.append(1))
    win.ctrl.set_preview_frame(np.zeros((90, 160, 3), dtype="uint8"))
    win.select(row_of("video"))
    app.processEvents()
    assert win.dock._timer.isActive()
    win.dock._tick()
    assert len(calls) == 1
    win.dock.surface._seq = -1
    win.select(row_of("connection"))      # 別ページ
    app.processEvents()
    assert not win.dock._timer.isActive()
    win.dock._tick()
    assert len(calls) == 1
    win.select(row_of("video"))
    win.showMinimized()
    app.processEvents()
    assert not win.dock._timer.isActive()
    win.dock._tick()
    assert len(calls) == 1
    win.showNormal()
    app.processEvents()
    assert win.dock._timer.isActive()
    win.dock_toggle.switch.setChecked(False)  # ドック非表示
    app.processEvents()
    assert not win.dock._timer.isActive()
    win.dock._tick()
    assert len(calls) == 1


def test_dock_text_shows_current_values(app):
    ctrl = Controller(persist=False, demo=True)
    ctrl.changed("lens", "wide")
    ctrl.changed("resolution", "1080p")
    ctrl.changed("fps", 60)
    assert preview_dock.dock_text(ctrl) == "%s · 1080p · 60fps" % preview_dock.t("labels.lens.wide")
