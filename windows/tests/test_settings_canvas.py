import json
from itertools import product

import numpy as np
import pytest

from phonecam.core import canvas, settings


def test_defaults_for_missing_or_corrupt_settings(tmp_path):
    path = tmp_path / "settings.json"
    loaded = settings.load_settings(path)
    assert {key: loaded[key] for key in ("mode", "bitrate", "camera_slot",
                                         "audio_camera", "camera_profiles")} == {
        "mode": "auto", "bitrate": 20, "camera_slot": 1,
        "audio_camera": 0, "camera_profiles": [],
    }
    path.write_text("{", encoding="utf-8")
    assert settings.load_settings(path) == loaded


@pytest.mark.parametrize(("key", "raw", "expected"), [
    ("bitrate", -1, 2), ("bitrate", 200, 50), ("bitrate", "12.7", 13),
    ("bitrate", "bad", 20), ("preview_fps", 58, 60), ("preview_fps", 32, 30),
    ("preview_fps", 0, settings.DEFAULT_SETTINGS["preview_fps"]),
    ("idle_stop_sec", "10", 10), ("idle_stop_sec", 0, 0),
    ("idle_stop_sec", 11, settings.DEFAULT_SETTINGS["idle_stop_sec"]),
])
def test_numeric_validation(key, raw, expected):
    assert settings.sanitize_settings({**settings.DEFAULT_SETTINGS, key: raw})[key] == expected


def test_migrate_preserves_first_camera_and_existing_destination(tmp_path):
    old = tmp_path / "old.json"
    new = tmp_path / "appdata" / "settings.json"
    old.write_text(json.dumps({"mode": "wifi", "ip": "192.168.1.2", "bitrate": 35}), encoding="utf-8")
    assert settings.migrate_legacy_settings(new, old)
    loaded = settings.load_settings(new)
    assert (loaded["mode"], loaded["mode_auto"], loaded["ip"], loaded["bitrate"]) == (
        "auto", True, "192.168.1.2", 35,
    )
    assert (loaded["camera_slot"], loaded["camera_profiles"], loaded["audio_camera"]) == (1, [], 0)
    loaded.update(mode="wifi", camera_profiles=[{"camera_slot": 3, "bitrate": 8}], audio_camera=1)
    settings.save_settings(loaded, str(new))
    assert not settings.migrate_legacy_settings(new, old)
    reloaded = settings.load_settings(new)
    assert reloaded["mode"] == "wifi"
    assert reloaded["camera_profiles"] == [{"camera_slot": 3, "bitrate": 8}]
    assert reloaded["audio_camera"] == 1


@pytest.mark.parametrize(("raw", "expected"), [(1, 2), (51, 50), ("11.7", 12), (None, 20)])
def test_bitrate_control(raw, expected):
    assert canvas.clamp_bitrate(raw) == expected
    assert canvas.build_control({"bitrate": raw}, "bitrate") == {"bitrate": expected}


def test_video_and_canvas_controls():
    config = {**settings.DEFAULT_SETTINGS, "resolution": "720p", "fps": "30",
              "orientation": "portrait", "canvas": "follow", "fit": "crop",
              "codec": "hevc", "mic_muted": True}
    assert canvas.build_control(config, "video") == {
        "width": 1280, "height": 720, "fps": 30, "lens": "wide", "orientation": "portrait",
        "outputCanvas": {"width": 1080, "height": 1920, "fit": "crop"},
    }
    assert canvas.build_control(config, "codec") == {"codec": "hevc", "keyframe": True}
    config.update(canvas="native", fit="invalid")
    assert canvas.build_control(config, "canvas") == {}
    assert canvas.build_control(config, "canvas", (640, 480)) == {
        "outputCanvas": {"width": 640, "height": 480, "fit": "pad"},
    }
    full = canvas.build_control(config, "all")
    assert full["micMuted"] is True
    assert full["codec"] == "hevc"
    assert "keyframe" not in full


@pytest.mark.parametrize(("mirror", "rotate"), tuple(product((False, True), repeat=2)))
@pytest.mark.parametrize("shape", [(6, 8), (6, 8, 3), (6, 8, 4)])
@pytest.mark.parametrize("noncontiguous", [False, True])
def test_flip_matches_numpy_slicing(shape, mirror, rotate, noncontiguous):
    img = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
    if noncontiguous:
        img = img[::2, ::2]
    expected = img[:, ::-1] if mirror else img
    if rotate:
        expected = expected[::-1, ::-1]
    result = canvas.flip_image(img, mirror, rotate)
    np.testing.assert_array_equal(result, expected)
    assert result.flags.c_contiguous


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="save_settings silently drops pathlib destinations")
def test_save_settings_accepts_path_objects(tmp_path):
    path = tmp_path / "settings.json"
    config = {**settings.DEFAULT_SETTINGS, "bitrate": 33}
    settings.save_settings(config, str(path))
    assert settings.load_settings(path)["bitrate"] == 33
    config["bitrate"] = 40
    settings.save_settings(config, path)
    assert settings.load_settings(path)["bitrate"] == 40
