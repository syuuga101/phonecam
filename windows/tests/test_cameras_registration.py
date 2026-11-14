import io
import sys
from unittest.mock import MagicMock, Mock

import pytest

from phonecam.controller import Controller
from phonecam.core import cameras, softcam_reg


def test_slots_preserve_profile_and_fill_gaps():
    group = cameras.CameraGroup(Controller(persist=False, demo=True))
    assert group.add({"camera_slot": 3}).get("camera_slot") == 3
    assert group.add({"camera_slot": 3}).get("camera_slot") == 2
    assert group.add().get("camera_slot") == 4
    assert not group.can_add
    assert group.add() is None
    profiles = group.primary.get("camera_profiles")
    assert [profile["camera_slot"] for profile in profiles] == [3, 2, 4]


def test_assign_and_unassign_enforce_one_camera_per_device(known_iphone):
    group = cameras.CameraGroup(Controller(persist=False, demo=True))
    primary, second = group.primary, group.add()
    device = cameras.read_device(known_iphone)
    assert group.assign(primary, device, usb_serial="serial")
    assert primary.get("ip") == "192.168.1.7"
    assert primary.get("pair_code") == "012345"
    assert group.users()[device.id] is primary
    assert not group.assign(second, device)
    assert not group.assign(second, usb_serial="serial")
    assert group.assign(primary)
    assert [primary.get(key) for key in ("target_device", "usb_serial", "ip", "pair_code")] == [""] * 4
    assert group.assign(second, device, usb_serial="serial")
    assert group.users()[device.id] is second


def test_usb_identity_routing_with_one_and_two_cameras(monkeypatch, known_iphone):
    group = cameras.CameraGroup(Controller(persist=False, demo=True))
    primary = group.primary
    identity = known_iphone["id"]
    monkeypatch.setattr(cameras, "usbmux_devices", lambda: ["first", "replacement"])
    mux = MagicMock()
    sock = mux.return_value.__enter__.return_value
    sock.makefile.return_value.__enter__.side_effect = lambda: io.BytesIO((identity + "\n").encode())
    monkeypatch.setattr(cameras, "usbmux_connect", mux)

    primary.changed("usb_serial", "unavailable")
    primary.changed("target_device", "fdec165c-f6b4-4e28-9e84-4cb9c7559d1f")
    assert primary.link._eligible_usb() == "first"
    assert group.routes.note_identity(primary.link, identity)
    mux.assert_not_called()

    second = group.add()
    assert primary.link._eligible_usb() is None
    assert not group.routes.note_identity(primary.link, identity)
    assert second.link._eligible_usb() is None
    group.routes.release(primary.link, ("usb", "first"))
    assert second.link._eligible_usb() == "first"
    assert group.routes.claim(second.link, ("usb", "first"))
    assert not group.routes.claim(primary.link, ("usb", "first"))
    assert group.routes.usb_identity("first") == identity
    assert mux.call_count == 2


@pytest.mark.parametrize(("registered", "installed", "expected"), [
    ("absent", True, "missing"),
    ("old", True, "outdated"),
    ("current", False, "outdated"),
    ("current", True, "ok"),
])
def test_registration_uses_revision_path(monkeypatch, tmp_path, registered, installed, expected):
    embedded = tmp_path / "embedded"
    # Keep filesystem and registry independent of the machine under test.
    monkeypatch.setattr(softcam_reg, "_embedded_dir", lambda: str(embedded))
    monkeypatch.setattr(softcam_reg, "open", Mock(side_effect=lambda *a, **kw: io.StringIO("AB12AB12AB12\n")),
                        raising=False)
    monkeypatch.setattr(softcam_reg, "softcam_install_root", lambda: str(tmp_path / "installed"))
    wanted = tmp_path / "installed" / "ab12ab12ab12" / "2" / "softcam.dll"
    monkeypatch.setattr(softcam_reg.os.path, "isfile",
                        lambda path: path == softcam_reg.softcam_path(2) or (installed and path == str(wanted)))
    winreg = MagicMock(HKEY_CLASSES_ROOT=1, KEY_READ=2, KEY_WOW64_32KEY=4)
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    if registered == "absent":
        winreg.OpenKey.side_effect = FileNotFoundError
    else:
        path = wanted if registered == "current" else wanted.parents[1].with_name("old-rev") / "2" / "softcam.dll"
        winreg.QueryValueEx.return_value = (f' "{path}" ', 0)
    monkeypatch.setattr(softcam_reg, "_log_outdated", Mock())
    assert softcam_reg.softcam_installed_path(2) == str(wanted)
    assert softcam_reg.registration_status(2) == expected
    assert softcam_reg._same_path(f'"{wanted}"', str(wanted))
    winreg.OpenKey.assert_called_once_with(1, f"CLSID\\{softcam_reg.camera_clsid(2)}\\InprocServer32", 0, 2)


def test_obs_display_names_match_build_slots_script():
    """画面に出す「OBS での名前」は、build-slots.ps1 が DLL に埋め込む DirectShow 名と一致する。"""
    import re
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "softcam_ext" / "build-slots.ps1").read_text(encoding="utf-8")
    match = re.search(r'\$display\s*=\s*if\s*\(\s*\$slot\s*-eq\s*1\s*\)\s*\{\s*"([^"]+)"\s*\}\s*else\s*\{\s*"([^"$]+)\$slot"\s*\}', script)
    assert match, "build-slots.ps1 の $display の書き方が変わった"
    first, prefix = match.group(1), match.group(2)
    group = cameras.CameraGroup(Controller(persist=False, demo=True))
    for slot in softcam_reg.CAMERA_SLOTS:
        expected = first if slot == 1 else prefix + str(slot)
        assert softcam_reg.camera_display_name(slot) == expected
        assert group.obs_name({"camera_slot": slot}) == expected
    assert [softcam_reg.camera_display_name(s) for s in softcam_reg.CAMERA_SLOTS] == ["PhoneCam", "PhoneCam 2", "PhoneCam 3", "PhoneCam 4"]
