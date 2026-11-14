"""SideStore 向けのセットアップ: iloader の検出と導入、iPhone の段階、導入チェック (診断)。"""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import phonecam_iphone as iphone
import phonecam_setup as ps

LOCALES = {lang: json.loads((Path(__file__).resolve().parents[1] / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
           for lang in ("ja", "en")}


# --- 必要なソフト -------------------------------------------------------------------

def test_components_have_iloader_and_no_altserver_or_icloud():
    ids = [c["id"] for c in ps.COMPONENTS]
    assert ids == ["camera", "vcredist", "itunes", "iloader", "vbcable"]
    assert "altserver" not in ps.SOURCES and "icloud" not in ps.SOURCES
    assert ps.INSTALL_ORDER == ["vcredist", "itunes", "iloader", "vbcable", "camera"]
    assert set(ps.GUIDED_IDS) == {"itunes", "iloader", "vbcable"}
    assert all(c.get("name") and c.get("purpose") for c in ps.COMPONENTS)


def test_iloader_source_is_official_msi_over_https():
    src = ps.SOURCES["iloader"]
    assert src["url"] == "https://github.com/nab138/iloader/releases/latest/download/iloader-windows-x64.msi"
    assert src["kind"] == "msi" and src["file"].endswith(".msi")
    assert src["page"].startswith("https://github.com/nab138/iloader")
    assert ps.SIGNATURES["msi"]


def test_detect_iloader_by_install_dir(monkeypatch, tmp_path):
    exe = tmp_path / "iloader" / "iloader.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(ps, "_program_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(ps, "_local_dirs", lambda: [])
    assert ps.find_iloader() == str(exe)
    assert ps.detect_iloader(entries=[])["state"] == ps.STATE_OK


def test_detect_iloader_in_local_appdata(monkeypatch, tmp_path):
    exe = tmp_path / "Programs" / "iloader" / "iloader.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(ps, "_program_dirs", lambda: [str(tmp_path / "nothing")])
    monkeypatch.setattr(ps, "_local_dirs", lambda: [str(tmp_path)])
    assert ps.find_iloader() == str(exe)


@pytest.mark.parametrize("name,expected", [("iloader", True), ("iLoader 2.3.10", True), ("iloader-extra", False),
                                           ("AltServer", False)])
def test_detect_iloader_by_uninstall_entry(monkeypatch, name, expected):
    monkeypatch.setattr(ps, "find_iloader", lambda: None)
    state = ps.detect_iloader(entries=[(name, "1.0")])["state"]
    assert state == (ps.STATE_OK if expected else ps.STATE_MISSING)


def test_detect_all_reports_iloader_and_not_removed_items(monkeypatch):
    monkeypatch.setattr(ps, "uninstall_entries", lambda: [("iloader", "2.3.10")])
    monkeypatch.setattr(ps, "find_iloader", lambda: None)
    out = ps.detect_all()
    assert out["iloader"]["state"] == ps.STATE_OK
    assert "altserver" not in out and "icloud" not in out


def test_selection_defaults_and_dependencies():
    statuses = {"camera": {"state": ps.STATE_OK}, "vcredist": {"state": ps.STATE_OK},
                "itunes": {"state": ps.STATE_MISSING}, "iloader": {"state": ps.STATE_MISSING},
                "vbcable": {"state": ps.STATE_MISSING}}
    sel = ps.default_selection(statuses)
    assert sel["iloader"] is True and sel["vbcable"] is False
    # iloader を入れるなら、足りない iTunes も入れる (入れる順に並ぶ)
    assert ps.expand_selection(["iloader"], statuses) == ["itunes", "iloader"]
    statuses["itunes"] = {"state": ps.STATE_OK}
    assert ps.expand_selection(["iloader"], statuses) == ["iloader"]
    statuses["itunes"] = {"state": ps.STATE_WARN}   # Store 版などは Apple 版へ入れ替える
    assert ps.expand_selection(["iloader"], statuses) == ["itunes", "iloader"]


def test_iloader_signature_policy_follows_altserver_style_unsigned_rule():
    ok, _msg, unsigned = ps.evaluate_signature("iloader", {"result": "unsigned"})
    assert ok and unsigned                          # 署名なしは、改ざん検知 (SHA-256) だけで実行
    ok, _msg, unsigned = ps.evaluate_signature("iloader", {"result": "valid", "signers": ["nab138"]})
    assert ok and not unsigned
    ok, _msg, _ = ps.evaluate_signature("iloader", {"result": "valid", "signers": ["Someone Else Ltd"]})
    assert not ok                                   # 署名があるのに発行元が違えば実行しない
    ok, _msg, _ = ps.evaluate_signature("iloader", {"result": "invalid"})
    assert not ok


def test_guided_flow_opens_iloader_msi_with_msiexec(tmp_path, monkeypatch):
    def download(url, path, kind, *, progress, cancel):
        assert url.endswith("iloader-windows-x64.msi") and kind == "msi"
        Path(path).write_bytes(b"\xd0\xcf\x11\xe0 fake msi")

    launcher = Mock(return_value=Mock())
    monkeypatch.setattr(ps, "check_installer_signature", lambda *a: (True, "", True))
    flow = ps.GuidedFlow(["iloader"], tmp_path, downloader=download,
                         detector=lambda: {"iloader": {"state": ps.STATE_MISSING}}, launcher=launcher)
    flow.prepare()
    assert flow.phase == flow.READY and flow.job["msi"] is True and flow.notice
    assert flow.launch()
    args = launcher.call_args.args
    assert args[0].lower().endswith("msiexec.exe") or args[0] == "msiexec"
    assert args[1][0] == "/i" and args[1][1].endswith("iloader-windows-x64.msi")


# --- iPhone の段階 -------------------------------------------------------------------

def test_iphone_steps_in_order():
    assert iphone.STEP_IDS == ["usb", "trust", "iloader", "devmode", "vpn", "sidestore", "phonecam", "test"]
    assert "itunes" not in iphone.STEP_IDS and "altstore" not in iphone.STEP_IDS
    assert ps.MANUAL_STEPS == ("iloader", "devmode", "vpn", "sidestore", "phonecam")
    assert iphone.AUTO_ADVANCE == ("usb", "trust")


def _all_iphone_keys():
    keys = [iphone.MIGRATE_TITLE, *iphone.MIGRATE_LINES, iphone.PAIRING_TITLE, *iphone.PAIRING_LINES,
            iphone.PREREQ_TITLE, iphone.PREREQ_INTRO, *iphone.PREREQ_LINES, iphone.PREREQ_NOTE]
    for step in iphone.IPHONE_STEPS:
        keys += [step["title"], *step["lines"]]
    return keys


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_iphone_text_keys_exist_in_both_languages(lang):
    missing = [k for k in _all_iphone_keys() if k not in LOCALES[lang]]
    assert not missing


def test_iphone_step_text_is_complete_and_mentions_sidestore_flow():
    ja = LOCALES["ja"]
    text = "\n".join(ja[k] for k in _all_iphone_keys())
    for word in ("Install SideStore (Stable)", "LocalDevVPN", "7 DAYS", "Sources", "Delete Stored Pairing",
                 "Manage Pairing File", "Place", "デベロッパモード", "3つまで"):
        assert word in text, word
    assert "AltServer" in ja["setup.iphone.migrate.l1"]     # 乗り換え案内だけが AltServer に触れる
    for step in iphone.IPHONE_STEPS:
        assert ja[step["title"]].strip()


def _snap(**kw):
    base = {"service": True, "usb": True, "trusted": True}
    base.update(kw)
    return base


def test_usb_and_trust_status_transitions():
    assert iphone.step_status("usb", {"service": False})[0] == iphone.ISSUE
    assert iphone.step_status("usb", _snap(usb=False))[0] == iphone.WAIT
    assert iphone.step_status("usb", _snap())[0] == iphone.DONE
    assert iphone.step_status("trust", _snap(usb=False))[0] == iphone.WAIT
    assert iphone.step_status("trust", _snap(trusted=False))[0] == iphone.WAIT
    assert iphone.step_status("trust", _snap(trusted=None))[0] == iphone.UNKNOWN
    assert iphone.step_status("trust", _snap())[0] == iphone.DONE


def test_iloader_step_needs_installed_then_confirmation():
    assert iphone.step_status("iloader", _snap(), {"iloader": False})[0] == iphone.ISSUE
    assert iphone.step_status("iloader", _snap(), {"iloader": True})[0] == iphone.MANUAL
    assert iphone.step_status("iloader", _snap(), {"iloader": True, "confirmed": True})[0] == iphone.DONE


@pytest.mark.parametrize("sid", ["devmode", "vpn", "sidestore", "phonecam"])
def test_manual_steps_move_from_manual_to_done_on_confirm(sid):
    assert iphone.step_status(sid, _snap(), {})[0] == iphone.MANUAL
    assert iphone.step_status(sid, _snap(), {"confirmed": True})[0] == iphone.DONE


def test_test_step_follows_usb_and_app_link():
    assert iphone.step_status("test", _snap(usb=False), {})[0] == iphone.WAIT
    assert iphone.step_status("test", _snap(), {})[0] == iphone.WAIT
    assert iphone.step_status("test", _snap(), {"app": True})[0] == iphone.DONE
    assert iphone.step_status("test", _snap(usb=False), {"link": True})[0] == iphone.DONE


def test_auto_advance_only_usb_and_trust_once():
    assert iphone.should_auto_advance("usb", iphone.DONE, set())
    assert iphone.should_auto_advance("trust", iphone.DONE, set())
    assert not iphone.should_auto_advance("usb", iphone.DONE, {"usb"})
    assert not iphone.should_auto_advance("iloader", iphone.DONE, set())
    assert not iphone.should_auto_advance("usb", iphone.WAIT, set())


def test_source_url_and_sidestore_deeplink():
    assert iphone.source_url("owner/repo") == "https://github.com/owner/repo/releases/latest/download/sidestore-source.json"
    assert iphone.source_deeplink("owner/repo") == "sidestore://source?url=" + iphone.source_url("owner/repo")
    assert iphone.source_url("") is None and iphone.source_deeplink("nope") is None


# --- 導入チェック (診断) ---------------------------------------------------------------

def sc_run(state="running", rc=0):
    def run(cmd, timeout=20):
        assert cmd[0] == "sc.exe" and cmd[-1] == "Apple Mobile Device Service"
        if state == "missing":
            return 1060, "[SC] OpenService FAILED 1060:"
        if state == "error":
            return None, ""
        return rc, "SERVICE_NAME: Apple Mobile Device Service\n        STATE              : %s" % (
            "4  RUNNING" if state == "running" else "1  STOPPED")
    return run


def diag(run=None, itunes=ps.STATE_OK, usb=None, iloader=ps.STATE_OK):
    usb = {"usb": True, "trusted": True} if usb is None else usb
    res = ps.run_diagnostics(run=run or sc_run(), itunes_status=lambda: {"state": itunes, "detail": "x"},
                             usb_snapshot=lambda: usb, iloader_status=lambda: {"state": iloader})
    return {r["id"]: r for r in res}


def test_diagnostics_checks_only_sidestore_items():
    res = diag()
    assert list(res) == ["itunes", "amds", "usb", "iloader"]
    assert all(r["state"] == ps.DIAG_OK for r in res.values())
    for removed in ("altserver", "bonjour", "network", "vpn", "firewall", "firewall_bonjour"):
        assert removed not in res
    assert ps.diagnostics_summary(list(res.values())) == ps.t("setup.wizard.diagnostics_summary_1")


def test_diagnostics_itunes_states():
    assert diag(itunes=ps.STATE_MISSING)["itunes"]["state"] == ps.DIAG_NG
    store = diag(itunes=ps.STATE_WARN)["itunes"]
    assert store["state"] == ps.DIAG_WARN and store["fix"]


@pytest.mark.parametrize("state,expected", [("running", ps.DIAG_OK), ("stopped", ps.DIAG_NG), ("missing", ps.DIAG_NG),
                                            ("error", ps.DIAG_WARN)])
def test_diagnostics_apple_mobile_device_service(state, expected):
    r = diag(run=sc_run(state))["amds"]
    assert r["state"] == expected
    assert (r["state"] == ps.DIAG_OK) or r["fix"]


def test_diagnostics_usb_and_trust():
    assert diag(usb={"usb": False})["usb"]["state"] == ps.DIAG_NG
    untrusted = diag(usb={"usb": True, "trusted": False})["usb"]
    assert untrusted["state"] == ps.DIAG_NG and untrusted["fix"]
    assert diag(usb={"usb": True, "trusted": None})["usb"]["state"] == ps.DIAG_OK   # 確認できないときは責めない


def test_diagnostics_iloader_and_summary():
    missing = diag(iloader=ps.STATE_MISSING)["iloader"]
    assert missing["state"] == ps.DIAG_NG and missing["fix"]
    results = list(diag(iloader=ps.STATE_MISSING, itunes=ps.STATE_WARN).values())
    assert "1" in ps.diagnostics_summary(results)
    seen = []
    ps.run_diagnostics(run=sc_run(), itunes_status=lambda: {"state": ps.STATE_OK}, usb_snapshot=lambda: {"usb": True},
                       iloader_status=lambda: {"state": ps.STATE_OK}, progress=seen.append)
    assert len([m for m in seen if m]) == 4 and seen[-1] == ""


def test_diagnostics_tolerates_failures():
    def boom():
        raise RuntimeError("x")
    res = ps.run_diagnostics(run=sc_run("error"), itunes_status=boom, usb_snapshot=boom, iloader_status=boom)
    states = {r["id"]: r["state"] for r in res}
    assert states["itunes"] == ps.DIAG_WARN and states["amds"] == ps.DIAG_WARN and states["iloader"] == ps.DIAG_WARN
    assert states["usb"] == ps.DIAG_NG
