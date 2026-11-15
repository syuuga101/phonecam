"""監査 #5 #6 #11 #12 #13 #15 の対応を確かめるテスト (権限境界・配布の検証・CI の固定)。"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

import phonecam_dshowctl as dshow
import phonecam_setup as ps
import phonecam_update as upd
from phonecam.core import softcam_reg

ROOT = Path(__file__).resolve().parents[2]


# --- #5 管理者ヘルパーのジョブ ----------------------------------------------------------

def _winget_job(tmp_path, **over):
    pkg = sorted(ps.ALLOWED_WINGET)[0]
    job = {"version": 1, "result": str(tmp_path / "result.json"), "progress": str(tmp_path / "progress.log"),
           "steps": [{"id": "vcredist", "type": "winget", "title": "x", "pkg": pkg, "timeout": 60}]}
    job.update(over)
    return job


def _write_job(tmp_path, job):
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_job_is_read_once_and_parsed_from_the_hashed_bytes(tmp_path, monkeypatch):
    path, sha = _write_job(tmp_path, _winget_job(tmp_path))
    opens = []
    real_open = open

    def spy(file, mode="r", *a, **kw):
        if str(file) == str(path):
            opens.append(mode)
            f = real_open(file, mode, *a, **kw)
            data = f.read() if "b" in mode else None
            f.close()
            # 読み終えた直後に別の内容へ差し替える (照合のあとの差し替え)
            real_open(file, "w", encoding="utf-8").write(json.dumps({"steps": [{"id": "x", "type": "evil"}]}))
            import io
            return io.BytesIO(data)
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(ps, "open", spy, raising=False)
    runner = Mock(return_value=Mock(returncode=0))
    assert ps.run_job(str(path), expected_sha=sha, runner=runner) == 0
    assert len(opens) == 1
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in result["steps"]] == ["vcredist"] and result["steps"][0]["ok"]
    runner.assert_called_once()


def test_job_without_or_with_wrong_hash_is_not_run(tmp_path):
    path, sha = _write_job(tmp_path, _winget_job(tmp_path))
    runner = Mock(return_value=Mock(returncode=0))
    assert ps.run_job(str(path), expected_sha=None, runner=runner) == 1
    assert ps.run_job(str(path), expected_sha="0" * 64, runner=runner) == 1
    runner.assert_not_called()


@pytest.mark.parametrize("mutate", [
    lambda j, d: j["steps"][0].update(type="shell", cmd="calc.exe"),
    lambda j, d: j["steps"][0].update(pkg="Evil.Package"),
    lambda j, d: j.update(result=str(d.parent / "elsewhere.json")),
    lambda j, d: j.update(progress=str(d / "sub" / ".." / ".." / "progress.log")),
    lambda j, d: j["steps"].append(dict(j["steps"][0])),                       # id の重複
    lambda j, d: j["steps"][0].update(timeout=10 ** 9),
    lambda j, d: j["steps"][0].update(id="camera", type="register_camera") or j["steps"].append({"id": "x", "type": "exe"}),
])
def test_job_outside_allowlist_is_rejected(tmp_path, mutate):
    job = _winget_job(tmp_path)
    mutate(job, tmp_path)
    path, sha = _write_job(tmp_path, job)
    runner = Mock(return_value=Mock(returncode=0))
    assert ps.run_job(str(path), expected_sha=sha, runner=runner) == 1
    runner.assert_not_called()


def _exe_step(tmp_path, cid="vcredist", **over):
    name = ps._allowed_exe_steps()[cid][0]
    exe = tmp_path / name
    exe.write_bytes(b"MZ" + b"0" * 200)
    step = {"id": cid, "type": "exe", "title": "t", "path": str(exe), "args": list(ps._allowed_exe_steps()[cid][1]),
            "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "ok_codes": [0], "timeout": 60}
    step.update(over)
    return step


def test_validate_job_accepts_what_the_app_builds(tmp_path):
    exe = tmp_path / "vc_redist.x64.exe"
    exe.write_bytes(b"MZ" + b"0" * 200)
    steps = [ps.step_register_camera(),
             ps.step_exe("vcredist", "t", str(exe), ps.SOURCES["vcredist"]["args"], ok_codes=ps.SOURCES["vcredist"]["ok_codes"]),
             ps.step_winget("itunes", "t", ps.SOURCES["itunes"]["winget"])]
    job = ps.build_job(steps, str(tmp_path / "result.json"), str(tmp_path / "progress.log"))
    clean = ps.validate_job(json.loads(json.dumps(job)), str(tmp_path / "job.json"))
    assert [s["type"] for s in clean["steps"]] == ["register_camera", "exe", "winget"]


@pytest.mark.parametrize("over", [
    {"args": ["/install", "/quiet", "&", "calc"]},
    {"args": []},
    {"retry_args": ["--evil"]},
    {"path": "OUTSIDE"},
    {"path": "RENAMED"},
    {"sha256": ""},
    {"ok_codes": [0, 5]},
    {"stage_dir": "OUTSIDE"},
])
def test_validate_job_rejects_unlisted_exe_steps(tmp_path, over):
    inside = tmp_path / "setup"
    inside.mkdir()
    step = _exe_step(inside)
    for k, v in list(over.items()):
        if v == "OUTSIDE":
            outside = tmp_path / ps._allowed_exe_steps()["vcredist"][0]
            outside.write_bytes(b"MZ" + b"1" * 200)
            over[k] = str(outside) if k == "path" else str(tmp_path)
        elif v == "RENAMED":
            (inside / "other.exe").write_bytes(b"MZ")
            over[k] = str(inside / "other.exe")
    step.update(over)
    job = {"version": 1, "result": str(inside / "result.json"), "progress": str(inside / "progress.log"), "steps": [step]}
    with pytest.raises(ps.JobRejected):
        ps.validate_job(job, str(inside / "job.json"))


# --- #6 仮想カメラの登録解除 -----------------------------------------------------------

@pytest.fixture
def softcam_env(tmp_path, monkeypatch):
    root = tmp_path / "ProgramData" / "PhoneCam" / "softcam"
    embedded = tmp_path / "embedded"
    root.mkdir(parents=True)
    embedded.mkdir()
    (embedded / "softcam.dll").write_bytes(b"genuine-64")
    (embedded / "softcam32.dll").write_bytes(b"genuine-32")
    monkeypatch.setattr(softcam_reg, "softcam_install_root", lambda: str(root))
    monkeypatch.setattr(softcam_reg, "softcam_path", lambda slot=1: str(embedded / "softcam.dll"))
    monkeypatch.setattr(softcam_reg, "softcam32_path", lambda slot=1: str(embedded / "softcam32.dll"))
    monkeypatch.setattr(softcam_reg, "_regsvr32_paths", lambda: ("x64.exe", "x86.exe"))
    monkeypatch.setattr(softcam_reg, "_cleanup_old_install_dirs", lambda keep_hash=None: None)
    monkeypatch.setattr(softcam_reg, "softcam_installed_path", lambda slot=1: str(root / "abc" / "softcam.dll"))
    return root, embedded


def test_trusted_dll_needs_managed_folder_and_known_hash(softcam_env, tmp_path):
    root, _ = softcam_env
    (root / "abc").mkdir()
    good = root / "abc" / "softcam.dll"
    good.write_bytes(b"genuine-64")
    assert softcam_reg.is_trusted_softcam_dll(str(good), 1, 64)
    good.write_bytes(b"tampered")
    assert not softcam_reg.is_trusted_softcam_dll(str(good), 1, 64)
    outside = tmp_path / "evil" / "softcam.dll"
    outside.parent.mkdir()
    outside.write_bytes(b"genuine-64")
    assert not softcam_reg.is_trusted_softcam_dll(str(outside), 1, 64)             # 中身が同じでも、管理下の外は不可
    sneaky = root / "abc" / ".." / ".." / "evil" / "softcam.dll"
    assert not softcam_reg.is_trusted_softcam_dll(str(sneaky), 1, 64)
    other = root / "abc" / "other.dll"
    other.write_bytes(b"genuine-64")
    assert not softcam_reg.is_trusted_softcam_dll(str(other), 1, 64)
    assert not softcam_reg.is_trusted_softcam_dll("softcam.dll", 1, 64)            # 相対パスは不可
    assert not softcam_reg.is_trusted_softcam_dll(None, 1, 64)


def test_unregister_runs_regsvr32_only_for_trusted_dll(softcam_env, monkeypatch, tmp_path):
    root, _ = softcam_env
    (root / "abc").mkdir()
    (root / "abc" / "softcam.dll").write_bytes(b"genuine-64")
    (root / "abc" / "softcam32.dll").write_bytes(b"genuine-32")
    evil = tmp_path / "evil.dll"
    evil.write_bytes(b"evil")
    registered = {64: str(evil), 32: str(root / "abc" / "softcam32.dll")}
    monkeypatch.setattr(softcam_reg, "machine_registered_path", lambda slot=1, bits=64: registered[bits])
    run, delete = Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", run)
    monkeypatch.setattr(softcam_reg, "delete_registration_keys", delete)
    assert softcam_reg.helper_unregister(1)
    called = [c.args[1] for c in run.call_args_list]
    assert str(evil) not in called                                               # 差し込まれた DLL は読み込まない
    assert str(root / "abc" / "softcam.dll") in called                           # 代わりに正規の DLL で解除する
    assert str(root / "abc" / "softcam32.dll") in called
    delete.assert_not_called()


def test_unregister_without_trusted_dll_only_deletes_keys(softcam_env, monkeypatch, tmp_path):
    evil = tmp_path / "evil.dll"
    evil.write_bytes(b"evil")
    monkeypatch.setattr(softcam_reg, "machine_registered_path", lambda slot=1, bits=64: str(evil))
    run, delete = Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", run)
    monkeypatch.setattr(softcam_reg, "delete_registration_keys", delete)
    assert softcam_reg.helper_unregister(1)
    run.assert_not_called()
    assert [c.args for c in delete.call_args_list] == [(1, 64), (1, 32)]
    delete.return_value = False
    assert not softcam_reg.helper_unregister(1)


def test_unregister_skips_views_that_are_not_registered(softcam_env, monkeypatch):
    monkeypatch.setattr(softcam_reg, "machine_registered_path", lambda slot=1, bits=64: None)
    run, delete = Mock(), Mock()
    monkeypatch.setattr(softcam_reg, "_run_regsvr32", run)
    monkeypatch.setattr(softcam_reg, "delete_registration_keys", delete)
    assert softcam_reg.helper_unregister(1)
    run.assert_not_called()
    delete.assert_not_called()


# --- #11 共有メモリのアクセス権と制御メッセージの検証 --------------------------------------

def test_sddl_grants_only_the_user():
    sid = "S-1-5-21-1111111111-2222222222-3333333333-1001"
    assert dshow.build_sddl(sid) == "D:P(A;;GA;;;%s)" % sid
    for bad in ("", "WD", "S-1-1-0)(A;;GA;;;WD", "S-1", None, "S-1-5-21-1;"):
        with pytest.raises(ValueError):
            dshow.build_sddl(bad)
    assert "WD" not in dshow.build_sddl(sid)           # Everyone を含まない


def test_existing_object_owner_must_be_the_user_admins_or_system():
    me = "S-1-5-21-1-2-3-1001"
    assert dshow.owner_allowed(me, me)
    assert dshow.owner_allowed("S-1-5-32-544", me)
    assert dshow.owner_allowed("S-1-5-18", me)
    assert not dshow.owner_allowed("S-1-5-21-1-2-3-1002", me)
    assert not dshow.owner_allowed("S-1-1-0", me)


def _request(backend, slot, value, flags, seq=1):
    bridge = dshow.ControlBridge(backend=backend)
    bridge._with_block(lambda ctx: True)            # 初期化
    off = dshow.HEADER_SIZE + dshow.SLOT_SIZE * slot
    cur = list(dshow._SLOT.unpack(backend.read(off, dshow.SLOT_SIZE)))
    cur[4], cur[5], cur[8] = value, flags, seq
    backend.write(off, dshow._SLOT.pack(*cur))
    hdr = list(dshow._HDR.unpack(backend.read(0, dshow._HDR.size)))
    hdr[2] = seq
    backend.write(0, dshow._HDR.pack(*hdr))
    return bridge


def test_out_of_range_values_are_clamped_to_the_fixed_slot_range():
    backend = dshow._BytesBackend()
    # 共有メモリ側の範囲 (min/max) まで書き換えられていても、固定の範囲に収める
    bridge = _request(backend, dshow.S_BRIGHTNESS, 2 ** 31 - 1, dshow.MANUAL)
    off = dshow.HEADER_SIZE
    cur = list(dshow._SLOT.unpack(backend.read(off, dshow.SLOT_SIZE)))
    cur[0], cur[1] = -(2 ** 31), 2 ** 31 - 1
    backend.write(off, dshow._SLOT.pack(*cur))
    assert bridge.poll() == [{"color": {"brightness": 0.5}}]
    bridge = _request(backend, dshow.S_ZOOM, -10 ** 9, dshow.MANUAL, seq=2)
    assert bridge.poll() == [{"zoom": 1.0}]


@pytest.mark.parametrize("slot,flags", [
    (dshow.S_BRIGHTNESS, 99), (dshow.S_BRIGHTNESS, 0), (dshow.S_BRIGHTNESS, -1),
    (dshow.S_BRIGHTNESS, dshow.AUTO),            # 明るさは自動に対応していない
    (dshow.S_ZOOM, dshow.AUTO),
])
def test_invalid_flags_are_dropped(slot, flags):
    backend = dshow._BytesBackend()
    bridge = _request(backend, slot, 10, flags)
    assert bridge.poll() == []


def test_valid_auto_request_still_works():
    backend = dshow._BytesBackend()
    bridge = _request(backend, dshow.S_FOCUS, 10, dshow.AUTO)
    assert bridge.poll() == [{"focus": {"mode": "auto"}}]


def test_cpp_header_has_no_null_dacl_and_uses_user_only_dacl():
    text = (ROOT / "windows" / "softcam_ext" / "PhoneCamControls.h").read_text(encoding="utf-8")
    assert "SetSecurityDescriptorDacl" not in text           # NULL DACL を作る呼び出し
    assert 'D:P(A;;GA;;;%s)' in text
    assert "ownerAllowed" in text and "kLimits" in text
    py = (ROOT / "windows" / "phonecam_dshowctl.py").read_text(encoding="utf-8")
    assert "SetSecurityDescriptorDacl" not in py


# --- #12 更新の検証 -------------------------------------------------------------------

def test_ed25519_matches_rfc8032_vector_1():
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert upd.ed25519_public_key(seed) == pub
    assert upd.ed25519_sign(seed, b"") == sig
    assert upd.ed25519_verify(pub, b"", sig)
    assert not upd.ed25519_verify(pub, b"x", sig)


def test_ed25519_agrees_with_cryptography_library():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization as ser
    seed = os.urandom(32)
    key = ed.Ed25519PrivateKey.from_private_bytes(seed)
    pub = key.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    msg = b"update manifest " * 7
    assert upd.ed25519_public_key(seed) == pub
    assert upd.ed25519_sign(seed, msg) == key.sign(msg)
    assert upd.ed25519_verify(pub, msg, key.sign(msg))
    bad = bytearray(key.sign(msg))
    bad[10] ^= 1
    assert not upd.ed25519_verify(pub, msg, bytes(bad))
    assert not upd.ed25519_verify(pub, msg, b"\0" * 64) and not upd.ed25519_verify(pub, msg, b"short")


def _signed(seed, version="9.9.9", sha=None, **over):
    manifest = {"asset": "PhoneCamReceiver.exe", "version": version, "sha256": sha or "a" * 64, "size": 123456}
    manifest.update(over)
    body = json.dumps(manifest).encode()
    return body, base64.b64encode(upd.ed25519_sign(seed, body)).decode()


def test_verify_manifest():
    seed = os.urandom(32)
    pub = upd.ed25519_public_key(seed)
    body, sig = _signed(seed)
    assert upd.verify_manifest(body, sig, "v9.9.9", pub)["sha256"] == "a" * 64
    with pytest.raises(upd.UpdateError):
        upd.verify_manifest(body + b" ", sig, "9.9.9", pub)                    # 本文が違う
    with pytest.raises(upd.UpdateError):
        upd.verify_manifest(body, sig, "9.9.9", upd.ed25519_public_key(os.urandom(32)))   # 別の鍵
    with pytest.raises(upd.UpdateError):
        upd.verify_manifest(body, sig, "9.9.8", pub)                           # 版が違う (古い版の署名の使い回し)
    for bad in (dict(sha="zz"), dict(asset="Other.exe"), dict(sha256=None)):
        b2, s2 = _signed(seed, **({"sha256": bad["sha"]} if "sha" in bad else bad))
        with pytest.raises(upd.UpdateError):
            upd.verify_manifest(b2, s2, "9.9.9", pub)
    with pytest.raises(upd.UpdateError):
        upd.verify_manifest(body, "not base64!!", "9.9.9", pub)


def _release(**asset):
    a = {"name": "PhoneCamReceiver.exe", "browser_download_url": "https://example.test/PhoneCamReceiver.exe", "size": 70000}
    a.update(asset)
    return upd.parse_release({"tag_name": "v9.9.9", "assets": [a]})


def test_parse_release_collects_manifest_urls_and_version():
    data = {"tag_name": "v9.9.9", "assets": [
        {"name": "PhoneCamReceiver.exe", "browser_download_url": "https://x.test/e.exe", "size": 5,
         "digest": "sha256:" + "b" * 64},
        {"name": upd.MANIFEST_NAME, "browser_download_url": "https://x.test/m.json"},
        {"name": upd.MANIFEST_SIG_NAME, "browser_download_url": "http://x.test/m.sig"},   # https 以外は無視
    ]}
    asset = upd.parse_release(data)["asset"]
    assert asset["manifest_url"] == "https://x.test/m.json" and "manifest_sig_url" not in asset
    assert asset["version"] == "9.9.9" and asset["sha256"] == "b" * 64


def test_update_without_hash_is_aborted_before_download(tmp_path):
    opener = Mock()
    asset = _release()["asset"]
    assert asset["sha256"] == ""
    with pytest.raises(upd.UpdateError):
        upd.download_asset(asset, str(tmp_path / "x.download"), opener=opener, public_key="")
    opener.assert_not_called()


def test_verify_file_requires_hash(tmp_path):
    f = tmp_path / "a.exe"
    f.write_bytes(b"MZ" + b"0" * upd.MIN_EXE_BYTES)
    with pytest.raises(upd.UpdateError):
        upd.verify_file(str(f), 0, "")
    upd.verify_file(str(f), 0, hashlib.sha256(f.read_bytes()).hexdigest())
    with pytest.raises(upd.UpdateError):
        upd.verify_file(str(f), 0, "0" * 64)


class _Resp:
    def __init__(self, data, url="https://x.test/f"):
        self.data, self.url, self.pos = data, url, 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        chunk = self.data[self.pos:self.pos + n] if n >= 0 else self.data[self.pos:]
        self.pos += len(chunk)
        return chunk


def test_signed_update_end_to_end_and_downgrade_paths(tmp_path):
    seed = os.urandom(32)
    pub = base64.b64encode(upd.ed25519_public_key(seed)).decode()
    exe = b"MZ" + os.urandom(upd.MIN_EXE_BYTES)
    sha = hashlib.sha256(exe).hexdigest()
    body, sig = _signed(seed, sha=sha, size=len(exe))
    files = {"https://x.test/e.exe": exe, "https://x.test/m.json": body, "https://x.test/m.sig": sig.encode()}
    opener = lambda req, timeout=0: _Resp(files[req.full_url], req.full_url)  # noqa: E731
    asset = {"name": upd.ASSET_NAME, "url": "https://x.test/e.exe", "size": len(exe), "sha256": "", "version": "9.9.9",
             "manifest_url": "https://x.test/m.json", "manifest_sig_url": "https://x.test/m.sig"}
    dest = tmp_path / "e.download"
    upd.download_asset(asset, str(dest), opener=opener, public_key=pub)
    assert dest.read_bytes() == exe
    # 公開鍵が入っているのに署名つきマニフェストが無い → 更新しない (署名を外して古い方式へ落とせない)
    dest.unlink()
    with pytest.raises(upd.UpdateError):
        upd.download_asset({k: v for k, v in asset.items() if not k.startswith("manifest")}, str(dest),
                           opener=opener, public_key=pub)
    # GitHub のハッシュとマニフェストが食い違う → 更新しない
    with pytest.raises(upd.UpdateError):
        upd.download_asset(dict(asset, sha256="c" * 64), str(dest), opener=opener, public_key=pub)
    # exe が署名したハッシュと違う → ダウンロードしたファイルを残さない
    files["https://x.test/e.exe"] = b"MZ" + os.urandom(upd.MIN_EXE_BYTES)
    with pytest.raises(upd.UpdateError):
        upd.download_asset(asset, str(dest), opener=opener, public_key=pub)
    assert not dest.exists()
    with pytest.raises(upd.UpdateError):
        upd.decode_public_key("not-a-key")
    assert upd.decode_public_key("") is None


def test_migration_mode_keeps_hash_only_flow(tmp_path):
    exe = b"MZ" + os.urandom(upd.MIN_EXE_BYTES)
    asset = {"name": upd.ASSET_NAME, "url": "https://x.test/e.exe", "size": len(exe),
             "sha256": hashlib.sha256(exe).hexdigest(), "version": "9.9.9"}
    dest = tmp_path / "e.download"
    upd.download_asset(asset, str(dest), opener=lambda req, timeout=0: _Resp(exe), public_key="")
    assert dest.read_bytes() == exe
    assert upd.UPDATE_PUBLIC_KEY == "" or upd.decode_public_key(upd.UPDATE_PUBLIC_KEY)


def test_manifest_script_signs_and_verifies(tmp_path, monkeypatch):
    import subprocess
    import sys
    exe = tmp_path / "PhoneCamReceiver.exe"
    exe.write_bytes(b"MZ" + b"1" * 1000)
    seed = os.urandom(32)
    env = dict(os.environ, UPDATE_SIGNING_KEY=base64.b64encode(seed).decode())
    out = tmp_path / "out"
    script = ROOT / ".github" / "scripts" / "make_update_manifest.py"
    r = subprocess.run([sys.executable, "-I", str(script), "--exe", str(exe), "--version", "1.2.3", "--out", str(out)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    body = (out / upd.MANIFEST_NAME).read_bytes()
    sig = (out / upd.MANIFEST_SIG_NAME).read_text().strip()
    info = upd.verify_manifest(body, sig, "1.2.3", upd.ed25519_public_key(seed))
    assert info["sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest() and info["size"] == 1002
    env.pop("UPDATE_SIGNING_KEY")
    (out / upd.MANIFEST_SIG_NAME).unlink()
    r = subprocess.run([sys.executable, "-I", str(script), "--exe", str(exe), "--version", "1.2.3", "--out", str(out)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0 and not (out / upd.MANIFEST_SIG_NAME).exists()
    # 公開鍵が入った版 (署名が必須) で、秘密鍵が無いときは止める
    wrapper = tmp_path / "w.py"
    wrapper.write_text("import runpy, sys; sys.path.insert(0, %r); import phonecam_update as u; u.UPDATE_PUBLIC_KEY = %r; "
                       "sys.argv[0] = %r; runpy.run_path(%r, run_name='__main__')" % (
                           str(ROOT / "windows"), base64.b64encode(upd.ed25519_public_key(seed)).decode(), str(script), str(script)))
    r = subprocess.run([sys.executable, str(wrapper), "--exe", str(exe), "--version", "1.2.3", "--out", str(out)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout
    r = subprocess.run([sys.executable, "-I", str(script), "--exe", str(exe), "--version", "1.2; rm", "--out", str(out)],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 1


# --- #13 VB-CABLE / iloader / 失効確認 -----------------------------------------------------

def test_pinned_sources_have_full_package_hashes():
    for cid in ("iloader", "vbcable"):
        assert re.fullmatch(r"[0-9a-f]{64}", ps.SOURCES[cid]["sha256"])
    assert "/latest/" not in ps.SOURCES["iloader"]["url"]            # 版を固定している


def test_verify_pinned_sha256(tmp_path):
    f = tmp_path / "p.zip"
    f.write_bytes(b"package")
    ps.verify_pinned_sha256(str(f), hashlib.sha256(b"package").hexdigest())
    ps.verify_pinned_sha256(str(f), None)
    with pytest.raises(ps.DownloadError):
        ps.verify_pinned_sha256(str(f), "0" * 64)
    assert not f.exists()                                            # 不一致のファイルは残さない


def _zip_bytes(files):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_is_verified_once_and_extracted_from_the_same_bytes(tmp_path):
    data = _zip_bytes({"VBCABLE_Setup_x64.exe": b"MZexe", "drv/vbaudio.sys": b"driver"})
    z = tmp_path / "pack.zip"
    z.write_bytes(data)
    verified = ps.read_verified_zip(str(z), hashlib.sha256(data).hexdigest())
    z.write_bytes(_zip_bytes({"VBCABLE_Setup_x64.exe": b"evil"}))     # 照合のあとの差し替え
    root = ps.safe_extract_zip(str(z), str(tmp_path / "out"), data=verified)
    assert (Path(root) / "VBCABLE_Setup_x64.exe").read_bytes() == b"MZexe"
    files = ps.zip_member_hashes(verified)
    assert set(files) == {"VBCABLE_Setup_x64.exe", "drv/vbaudio.sys"}
    assert ps.verify_tree(root, files)
    (Path(root) / "drv" / "vbaudio.sys").write_bytes(b"swapped")
    assert not ps.verify_tree(root, files)                           # 同梱ドライバーの差し替えを検出
    (Path(root) / "drv" / "vbaudio.sys").write_bytes(b"driver")
    (Path(root) / "extra.dll").write_bytes(b"x")
    assert not ps.verify_tree(root, files)                           # 余分なファイル
    with pytest.raises(ps.DownloadError):
        ps.read_verified_zip(str(z), "0" * 64)


def test_staged_installer_folder_must_match_the_verified_package(tmp_path):
    root = tmp_path / "vb"
    root.mkdir()
    (root / "setup.exe").write_bytes(b"MZ")
    (root / "d.sys").write_bytes(b"drv")
    files = {"setup.exe": hashlib.sha256(b"MZ").hexdigest(), "d.sys": hashlib.sha256(b"drv").hexdigest()}
    path, cleanup = ps._stage_for_run(str(root / "setup.exe"), str(root), files)
    cleanup()
    (root / "d.sys").write_bytes(b"other")
    with pytest.raises(RuntimeError):
        ps._stage_for_run(str(root / "setup.exe"), str(root), files)


def test_job_files_are_validated(tmp_path):
    inside = tmp_path / "PhoneCamSetup"
    inside.mkdir()
    exe = inside / "vbcable" / "vbcable_setup_x64.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    step = ps.step_exe("vbcable", "t", str(exe), ["-i", "-h"], timeout=300, lenient=True, reboot=True, stage_dir=str(exe.parent))
    step["files"] = {"vbcable_setup_x64.exe": step["sha256"]}
    job = ps.build_job([step], str(inside / "result.json"), str(inside / "progress.log"))
    clean = ps.validate_job(json.loads(json.dumps(job)), str(inside / "job.json"))
    assert clean["steps"][0]["files"] == step["files"]
    job["steps"][0]["files"] = {"a": "nothex"}
    with pytest.raises(ps.JobRejected):
        ps.validate_job(job, str(inside / "job.json"))


def test_revocation_is_checked_and_unknown_revocation_is_not_allowed():
    assert ps.WTD_REVOKE_WHOLECHAIN == 1
    src = (Path(ps.__file__)).read_text(encoding="utf-8")
    assert "data.fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN" in src and "WTD_REVOKE_NONE" not in src.split("def verify_authenticode")[1]
    assert ps.classify_trust_result(0x80092013, 0) == "revocation_unknown"
    assert ps.classify_trust_result(0x80092012, 0) == "revocation_unknown"
    assert ps.classify_trust_result(0x800B010C, 0) == "invalid"       # 失効済み
    ok, message, unsigned = ps.evaluate_signature("itunes", {"result": "revocation_unknown", "signers": ["Apple Inc."]})
    assert not ok and not unsigned and message


# --- #15 CI --------------------------------------------------------------------------

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.skipif(not WORKFLOWS, reason="workflows are not part of this checkout")
def test_every_action_is_pinned_to_a_commit_sha():
    for path in WORKFLOWS:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"\s*(?:-\s*)?uses:\s*(\S+)", line)
            if m and not m.group(1).startswith("./"):
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", m.group(1)), "%s:%d %s" % (path.name, n, line.strip())
                assert "#" in line, "%s:%d タグをコメントに書いてください" % (path.name, n)


@pytest.mark.skipif(not WORKFLOWS, reason="workflows are not part of this checkout")
def test_no_expression_is_expanded_inside_run_scripts():
    allowed = re.compile(r"\$\{\{\s*(env|secrets)\.")
    for path in WORKFLOWS:
        lines = path.read_text(encoding="utf-8").splitlines()
        in_run, indent = False, 0
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            cur = len(line) - len(line.lstrip())
            if in_run and stripped and cur <= indent:
                in_run = False
            if not in_run:
                m = re.match(r"(\s*)(?:-\s*)?run:\s*(.*)", line)
                if m:
                    in_run, indent = m.group(2).strip() in ("|", ">", "|-", ">-"), len(m.group(1)) + (2 if "- run" in line else 0)
                    body = "" if in_run else m.group(2)
                else:
                    continue
            else:
                body = line
            if "${{" in body:
                assert allowed.search(body), "%s:%d run の中で式を展開しています: %s" % (path.name, n, body.strip())


@pytest.mark.skipif(not WORKFLOWS, reason="workflows are not part of this checkout")
def test_softcam_and_xcodegen_are_pinned():
    win = (ROOT / ".github" / "workflows" / "windows.yml").read_text(encoding="utf-8")
    assert re.search(r"SOFTCAM_SHA:\s*[0-9a-f]{40}", win) and "--branch" not in win
    ios = (ROOT / ".github" / "workflows" / "ios.yml").read_text(encoding="utf-8")
    assert "brew install xcodegen" not in ios and re.search(r"XCODEGEN_SHA256:\s*[0-9a-f]{64}", ios)
    for name in ("release.yml", "ios.yml"):
        assert "[0-9]+\\.[0-9]+\\.[0-9]+" in (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_lock_files_pin_every_requirement_with_hashes():
    locks = [p for p in (ROOT / "windows").glob("requirements*.lock")]
    if not locks:
        pytest.skip("lock files are not part of this checkout")
    for path in locks:
        text = path.read_text(encoding="utf-8")
        entries = re.findall(r"^([A-Za-z0-9_.\-]+)==\S+", text, re.M)
        assert entries
        for name in entries:
            block = re.search(r"^%s==[^\n]*(?:\n[ \t]+[^\n]*)*" % re.escape(name), text, re.M | re.I).group(0)
            assert "--hash=sha256:" in block, "%s の %s にハッシュがありません" % (path.name, name)
