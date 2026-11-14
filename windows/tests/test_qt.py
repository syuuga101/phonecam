from unittest.mock import Mock

import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

import phonecam_setup as setup
from phonecam.controller import Controller
from phonecam.core.cameras import CameraGroup
from phonecam.qt.multicam import AddTile
from phonecam.qt.pages import cameras
from phonecam.qt.setup_wizard import SetupWizardDialog


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


def test_camera_page_keeps_one_tile_per_camera_and_hides_add_at_limit(app):
    primary = Controller(persist=False, demo=True)
    group = CameraGroup(primary)
    group.add()
    page = cameras.build(primary)
    page.show()
    try:
        assert [tile.camera for tile in page.tiles] == group.cameras
        assert any(not tile.isHidden() for tile in page.findChildren(AddTile))
        group.add()
        group.add()
        assert len(page.tiles) == 4
        assert [tile.camera for tile in page.tiles] == group.cameras
        assert not any(tile.isVisible() for tile in page.findChildren(AddTile))
    finally:
        page.close()
        page.deleteLater()


def test_wizard_next_moves_welcome_to_check(app):
    controller = setup.SetupController(Mock(), engine=Mock())
    controller.checked = True
    controller.statuses = {component["id"]: {"state": setup.STATE_OK, "detail": ""}
                           for component in setup.COMPONENTS}
    controller.apply_defaults()
    wizard = SetupWizardDialog(controller)
    wizard.show()
    try:
        assert wizard.page == setup.PAGE_WELCOME
        assert wizard.next_btn.isEnabled()
        wizard.next_btn.click()
        assert wizard.page == setup.PAGE_CHECK
        assert wizard.panel_card is not None
    finally:
        wizard.close()
        wizard.deleteLater()
