"""監査 #14 / #17 / #20 / #10 の純粋ロジックのテスト (PySide6 なしで動く)。"""
import json
import math
import os
import struct
from unittest.mock import Mock

import pytest

from phonecam.core import protocol, softcam_reg
from phonecam.core.wireless import token_matches
from phonecam.i18n import t
from phonecam.labels import clip_text, mic_state_key, mic_state_text


@pytest.mark.parametrize(("enabled", "pc", "phone", "key"), [
    (False, False, False, "off"),
    (False, True, True, "off"),
    (True, False, True, "phone"),
    (True, True, False, "muted"),
    (True, True, True, "both"),
    (True, False, False, "on"),
])
def test_mic_state_four_plus_both(enabled, pc, phone, key):
    assert mic_state_key(enabled, pc, phone) == key
    assert mic_state_text(enabled, pc, phone) == t("compact.sum.mic." + key)


def test_mic_texts_exist_in_both_languages():
    for lang in ("ja", "en"):
        path = os.path.join(os.path.dirname(__file__), "..", "locales", lang + ".json")
        data = json.load(open(path, encoding="utf-8"))
        for key in ("off", "phone", "muted", "both", "on"):
            assert data["compact.sum.mic." + key]
        for key in ("video.vcam.partial", "video.vcam.partial_note", "video.vcam.status.partial",
                    "multi.vcam.ready_x64", "setup.first_run.bar_partial", "setup.first_run.bar_retry_btn"):
            assert data[key]


def test_clip_text_limits_and_flattens():
    assert clip_text("a" * 100, 40) == "a" * 39 + "…"
    assert clip_text("名前\n<b>x</b>\x00") == "名前 <b>x</b>"
    assert clip_text(None) == ""
    assert len(clip_text("<img src=x width=1000 height=1000>" * 5)) <= 40


def test_token_with_surrogate_is_rejected_not_raised():
    assert token_matches("abc", "abc") is True
    assert token_matches("abd", "abc") is False
    assert token_matches("\ud800", "abc") is False
    assert token_matches(123, "abc") is False
    assert token_matches(None, "abc") is False


def test_non_finite_and_bad_channels_are_dropped():
    assert protocol.parse_pong(b'{"pong": 1.5, "t": 2.0}') == (1.5, 2.0)
    assert protocol.parse_pong(b'{"pong": Infinity, "t": 2.0}') is None
    assert protocol.parse_pong(b'{"pong": 1.0, "t": NaN}') is None
    assert protocol.parse_timing(struct.pack(">dd", 1.0, 2.0)) == (1.0, 2.0)
    assert protocol.parse_timing(struct.pack(">dd", float("inf"), 2.0)) is None
    assert protocol.parse_timing(struct.pack(">dd", 1.0, float("-inf"))) is None
    pytest.importorskip("numpy")
    pcm = struct.pack("<4h", 1, 2, 3, 4)
    good = struct.pack(">dIB", 1.0, 48000, 1) + pcm
    assert protocol.parse_audio(good) is not None
    assert protocol.parse_audio(struct.pack(">dIB", 1.0, 48000, 0) + pcm) is None
    assert protocol.parse_audio(struct.pack(">dIB", float("inf"), 48000, 1) + pcm) is None


# --- #14 仮想カメラ登録 -------------------------------------------------------------

def _setup_dlls(monkeypatch, tmp_path, with32=True):
    embedded = tmp_path / "emb"
    embedded.mkdir()
    (embedded / "softcam.dll").write_bytes(b"x64-new")
    if with32:
        (embedded / "softcam32.dll").write_bytes(b"x86-new")
    monkeypatch.setattr(softcam_reg, "_embedded_dir", lambda: str(embedded))
    monkeypatch.setattr(softcam_reg, "softcam_install_root", lambda: str(tmp_path / "inst"))
    monkeypatch.setattr(softcam_reg, "softcam_rev", lambda slot=1: "ab12ab12ab12")
    monkeypatch.setattr(softcam_reg, "_regsvr32_paths", lambda: ("x64.exe", "x86.exe"))
    monkeypatch.setattr(softcam_reg, "_cleanup_old_install_dirs", Mock())
    return tmp_path / "inst" / "ab12ab12ab12"


def test_register_reports_both_views(monkeypatch, tmp_path):
    dst = _setup_dlls(monkeypatch, tmp_path)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", lambda exe, dll, unregister=False: True)
    result = softcam_reg.helper_register(1)
    assert result and result.x64 is True and result.x86 is True and not result.partial
    assert (dst / "softcam.dll").read_bytes() == b"x64-new"


def test_register_32bit_failure_is_reported_separately(monkeypatch, tmp_path):
    _setup_dlls(monkeypatch, tmp_path)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", lambda exe, dll, unregister=False: exe == "x64.exe")
    result = softcam_reg.helper_register(1)
    assert result.x64 is True and result.x86 is False and result.partial and bool(result)
    assert softcam_reg.helper_main(["--register"]) == softcam_reg.EXIT_REGISTER_PARTIAL


def test_register_without_32bit_dll_has_no_x86_result(monkeypatch, tmp_path):
    _setup_dlls(monkeypatch, tmp_path, with32=False)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", lambda exe, dll, unregister=False: True)
    result = softcam_reg.helper_register(1)
    assert result.x64 is True and result.x86 is None and not result.partial


def test_register_fails_when_old_dll_cannot_be_replaced(monkeypatch, tmp_path):
    dst = _setup_dlls(monkeypatch, tmp_path)
    dst.mkdir(parents=True)
    (dst / "softcam.dll").write_bytes(b"x64-OLD")          # 使用中で上書きできない古い DLL
    real_copy = softcam_reg.shutil.copyfile
    monkeypatch.setattr(softcam_reg.shutil, "copyfile",
                        Mock(side_effect=lambda s, d: (_ for _ in ()).throw(PermissionError()) if d.endswith("softcam.dll") else real_copy(s, d)))
    run = Mock(return_value=True)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", run)
    monkeypatch.setattr(softcam_reg, "_error_box", Mock())   # Windows では本物の MessageBox が出て止まる
    result = softcam_reg.helper_register(1)
    assert result.x64 is False and not result
    assert all(call.args[1] != str(dst / "softcam.dll") for call in run.call_args_list)   # 古い DLL は登録しない
    assert softcam_reg.helper_main(["--register"]) == 1
