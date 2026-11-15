# -*- coding: utf-8 -*-
"""PhoneCam 受信アプリの更新確認と自己更新 (GUI なしの部分)。

- GitHub Releases の最新版を確認する
- PhoneCamReceiver.exe をダウンロードして検証する (https のみ・サイズ・SHA-256)
- 動いている exe を「名前変更 → 新しいファイルを元の名前へ」で入れ替える
画面は phonecam/qt 側。ここは純粋な関数とクラスだけにしてテストしやすくしている。
"""

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

CHECK_INTERVAL_SEC = 6 * 3600
CHECK_TIMEOUT_SEC = 8
DOWNLOAD_TIMEOUT_SEC = 30
ASSET_NAME = "PhoneCamReceiver.exe"
OLD_SUFFIX = ".old.exe"
MIN_EXE_BYTES = 64 * 1024

# 更新の署名 (Ed25519)。リリースには update-manifest.json (version・exe の SHA-256・サイズ) と、その署名
# update-manifest.json.sig (Base64) が付く (release.yml が秘密鍵 UPDATE_SIGNING_KEY で署名する。手順は docs/release-signing.md)。
# UPDATE_PUBLIC_KEY (Ed25519 の公開鍵 32 バイトを Base64 にしたもの) が空の間は、署名の仕組みを使わずに
# 「GitHub が返す SHA-256 と一致すること」だけで更新する (ハッシュが無ければ更新しない)。
# 公開鍵を入れたら、署名つきのマニフェストが無い・署名が合わない更新はすべて中止する。
UPDATE_PUBLIC_KEY = ""
MANIFEST_NAME = "update-manifest.json"
MANIFEST_SIG_NAME = "update-manifest.json.sig"
MAX_MANIFEST_BYTES = 64 * 1024


class UpdateError(Exception):
    """利用者にそのまま見せてよい文言を持つ例外。"""


# ---------------------------------------------------------------------------
# バージョン
# ---------------------------------------------------------------------------

def parse_version(text):
    """'v1.4.1' → (1, 4, 1)。数字で始まらない ('dev' など) は None。'1.4' は (1, 4, 0)、'1.4.1-beta' は (1, 4, 1)。"""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", s)
    if not m:
        return None
    return tuple(int(g) if g is not None else 0 for g in m.groups())


def is_newer(latest, current):
    """latest が current より新しいか。どちらかが数値のバージョンでなければ False (dev ビルドでは通知しない)。"""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def same_major_minor(a, b):
    """どちらかが読めなければ True (違うとは言えない)。"""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return True
    return pa[:2] == pb[:2]


def display_version(text):
    p = parse_version(text)
    if p is None:
        return str(text or "")
    s = str(text).strip()
    return s[1:] if s[:1] in ("v", "V") else s


def skip_allows(tag, skip_version=""):
    """「通知しない」にした版でなければ True。"""
    skip = parse_version(skip_version)
    return not (skip is not None and parse_version(tag) == skip)


def should_notify(latest_tag, current, skip_version=""):
    """起動時などに自動で知らせるか。スキップ済みの版は知らせない (より新しい版が出れば知らせる)。"""
    if not is_newer(latest_tag, current):
        return False
    return skip_allows(latest_tag, skip_version)


# ---------------------------------------------------------------------------
# リリース情報
# ---------------------------------------------------------------------------

def release_url(repo):
    return "https://api.github.com/repos/%s/releases/latest" % repo


def parse_release(data):
    """GitHub のリリース JSON から必要な項目だけ取り出す。壊れていれば UpdateError。"""
    if not isinstance(data, dict):
        raise UpdateError("更新情報を読み取れませんでした")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        raise UpdateError("更新情報のバージョンを読み取れませんでした")
    html_url = data.get("html_url")
    if not (isinstance(html_url, str) and html_url.lower().startswith("https://")):
        html_url = ""
    body = data.get("body")
    asset = None
    assets = data.get("assets")
    extra = {}
    if isinstance(assets, list):
        for a in assets:
            if isinstance(a, dict) and a.get("name") in (MANIFEST_NAME, MANIFEST_SIG_NAME):
                u = a.get("browser_download_url")
                if isinstance(u, str) and u.lower().startswith("https://"):
                    extra["manifest_url" if a.get("name") == MANIFEST_NAME else "manifest_sig_url"] = u
        for a in assets:
            if not isinstance(a, dict) or a.get("name") != ASSET_NAME:
                continue
            url = a.get("browser_download_url")
            if not isinstance(url, str) or not url:
                continue
            size = a.get("size")
            size = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 0
            digest = a.get("digest")
            sha256 = ""
            if isinstance(digest, str) and digest.lower().startswith("sha256:"):
                h = digest[7:].strip().lower()
                if re.fullmatch(r"[0-9a-f]{64}", h):
                    sha256 = h
            asset = {"name": ASSET_NAME, "url": url, "size": size, "sha256": sha256, "version": display_version(tag)}
            asset.update(extra)
            break
    return {
        "tag": tag,
        "version": display_version(tag),
        "name": data.get("name") if isinstance(data.get("name"), str) else "",
        "html_url": html_url,
        "body": body if isinstance(body, str) else "",
        "asset": asset,
    }


def fetch_latest(repo, version, timeout=CHECK_TIMEOUT_SEC, opener=None):
    """最新リリースを取得して parse_release の結果を返す。失敗は UpdateError (文言は利用者向け)。"""
    if not repo or "/" not in repo:
        raise UpdateError("確認先が設定されていません")
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(release_url(repo), headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "PhoneCam/%s" % version,
    })
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read(2 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("公開されたリリースがまだありません")
        if e.code in (403, 429):
            raise UpdateError("確認の回数制限に達しました。しばらくしてからお試しください")
        raise UpdateError("確認できませんでした (HTTP %s)" % e.code)
    except Exception:
        raise UpdateError("ネットワークに接続できませんでした")
    return parse_release(data)


# ---------------------------------------------------------------------------
# 更新内容 (Markdown → 平文)
# ---------------------------------------------------------------------------

def markdown_to_text(md):
    if not isinstance(md, str):
        return ""
    text = md.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"```[^\n]*\n?", "", text)                      # コードフェンス
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)         # 画像
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # [text](url) → text
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    text = re.sub(r"<[^>\n]+>", "", text)                         # HTML タグ
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)             # 見出し
        line = re.sub(r"^\s{0,3}>\s?", "", line)                  # 引用
        line = re.sub(r"^(\s*)[-*+]\s+(\[[ xX]\]\s+)?", lambda m: m.group(1) + "・", line)  # 箇条書き
        line = re.sub(r"^\s*([-*_])(\s*\1){2,}\s*$", "", line)    # 区切り線
        line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)           # 太字
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)  # 斜体
        line = re.sub(r"`([^`]*)`", r"\1", line)                  # コード
        line = re.sub(r"~~(.+?)~~", r"\1", line)
        lines.append(line.rstrip())
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out


# ---------------------------------------------------------------------------
# Ed25519 (RFC 8032) — 外部ライブラリを増やさないための純 Python 実装
# ---------------------------------------------------------------------------
# 検証は更新のときに1回だけなので速度は問題にならない。署名 (sign) は CI でマニフェストに署名するときだけ使う。
# RFC 8032 のテストベクトルと、cryptography ライブラリの結果との一致を tests/test_hardening.py で確かめている。

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(pt):
    zi = _inv(pt[2])
    x, y = pt[0] * zi % _P, pt[1] * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h512_modq(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public_key(seed):
    """秘密鍵 (32 バイトの種) → 公開鍵 (32 バイト)。"""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    return _compress(_mul(_expand(seed)[0], _G))


def ed25519_sign(seed, message):
    """CI でマニフェストに署名する。64 バイトの署名を返す。"""
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = _h512_modq(prefix + message)
    rs = _compress(_mul(r, _G))
    s = (r + _h512_modq(rs + pub + message) * a) % _Q
    return rs + int.to_bytes(s, 32, "little")


def ed25519_verify(public_key, message, signature):
    """署名が正しければ True。形式がおかしい入力も False (例外は出さない)。"""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a = _decompress(public_key)
        rs = signature[:32]
        r = _decompress(rs)
        s = int.from_bytes(signature[32:], "little")
        if a is None or r is None or s >= _Q:
            return False
        h = _h512_modq(rs + public_key + message)
        return _equal(_mul(s, _G), _add(r, _mul(h, a)))
    except Exception:
        return False


def decode_public_key(text):
    """Base64 の公開鍵 → 32 バイト。空なら None (署名なしの移行モード)。形式が不正なら UpdateError。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except Exception:
        raw = b""
    if len(raw) != 32:
        raise UpdateError("更新の署名用の公開鍵が正しくありません。安全のため更新しません")
    return raw


def verify_manifest(manifest_bytes, signature_b64, version, public_key):
    """署名つきマニフェストを確かめて {"version","sha256","size"} を返す。
    署名が合わない・版が違う・exe の名前や SHA-256 の形が違うときは UpdateError。
    manifest_bytes は署名した JSON のバイト列そのもの (読み直さず、そのバイト列で署名の確認と解釈の両方をする)。"""
    try:
        sig = base64.b64decode((signature_b64 or b"").strip(), validate=True)
    except Exception:
        sig = b""
    if not ed25519_verify(public_key, manifest_bytes, sig):
        raise UpdateError("更新情報の署名が正しくありません。安全のため更新しません")
    try:
        data = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        raise UpdateError("更新情報を読み取れませんでした")
    if not isinstance(data, dict) or data.get("asset") != ASSET_NAME:
        raise UpdateError("更新情報の内容が正しくありません。安全のため更新しません")
    if parse_version(str(data.get("version"))) != parse_version(version) or parse_version(version) is None:
        raise UpdateError("更新情報のバージョンが一致しません。安全のため更新しません")
    sha = data.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise UpdateError("更新情報にハッシュがありません。安全のため更新しません")
    size = data.get("size")
    size = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 0
    return {"version": str(data.get("version")), "sha256": sha.lower(), "size": size}


# ---------------------------------------------------------------------------
# ダウンロードと検証
# ---------------------------------------------------------------------------

class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith("https://"):
            raise UpdateError("安全でないURL (https以外) へ転送されたため中止しました")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener(req, timeout):
    return urllib.request.build_opener(_HttpsOnlyRedirect).open(req, timeout=timeout)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path, size=0, sha256=""):
    """サイズ・SHA-256・exe の先頭 (MZ) を確かめる。SHA-256 が無いときは確かめられないので UpdateError (更新しない)。"""
    if not sha256:
        raise UpdateError("公開されたハッシュ (SHA-256) が無いため、更新ファイルを確かめられません。安全のため更新しません")
    try:
        actual = os.path.getsize(path)
    except OSError:
        raise UpdateError("ダウンロードしたファイルが見つかりません")
    if actual < MIN_EXE_BYTES:
        raise UpdateError("ダウンロードしたファイルが小さすぎます")
    if size and actual != size:
        raise UpdateError("ダウンロードが途中で切れました（サイズが一致しません）")
    if sha256_file(path).lower() != sha256.lower():
        raise UpdateError("ダウンロードしたファイルが公開されたものと一致しません（SHA-256の不一致）。安全のため更新しません")
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise UpdateError("ダウンロードしたファイルの形式が正しくありません")


def _fetch_small(url, limit, opener, timeout, version):
    """マニフェストなど小さいファイルを https で読む (limit バイトを超えたら中止)。"""
    if not str(url).lower().startswith("https://"):
        raise UpdateError("安全でないURL (https以外) からはダウンロードしません")
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneCam/%s" % version})
    try:
        with opener(req, timeout=timeout) as resp:
            final = getattr(resp, "geturl", lambda: url)()
            if isinstance(final, str) and not final.lower().startswith("https://"):
                raise UpdateError("安全でないURL (https以外) へ転送されたため中止しました")
            data = resp.read(limit + 1)
    except UpdateError:
        raise
    except Exception:
        raise UpdateError("更新情報をダウンロードできませんでした")
    if len(data) > limit:
        raise UpdateError("更新情報が大きすぎます。安全のため更新しません")
    return data


def expected_from_release(asset, opener=None, timeout=CHECK_TIMEOUT_SEC, version="dev", public_key=None):
    """更新ファイルが満たすべき (SHA-256, サイズ) を決める。
    - 公開鍵が埋め込まれているとき: 署名つきマニフェストが必須。署名・版・SHA-256 を確かめ、GitHub のハッシュがあれば一致も要求する
    - 公開鍵が空のとき (移行中): GitHub が返す SHA-256 が必須 (無ければ UpdateError で更新しない)"""
    asset = asset if isinstance(asset, dict) else {}
    key = decode_public_key(UPDATE_PUBLIC_KEY if public_key is None else public_key)
    sha, size = str(asset.get("sha256") or "").lower(), int(asset.get("size") or 0)
    if key is None:
        if not sha:
            raise UpdateError("公開されたハッシュ (SHA-256) が無いため、更新ファイルを確かめられません。安全のため更新しません")
        return sha, size
    opener = opener or _default_opener
    if not asset.get("manifest_url") or not asset.get("manifest_sig_url"):
        raise UpdateError("署名つきの更新情報がありません。安全のため更新しません")
    manifest = _fetch_small(asset["manifest_url"], MAX_MANIFEST_BYTES, opener, timeout, version)
    sig = _fetch_small(asset["manifest_sig_url"], 1024, opener, timeout, version)
    info = verify_manifest(manifest, sig.decode("ascii", "ignore"), asset.get("version") or "", key)
    if sha and sha != info["sha256"]:
        raise UpdateError("更新情報のハッシュが公開されたものと一致しません。安全のため更新しません")
    return info["sha256"], (info["size"] or size)


def download_asset(asset, dest, progress=None, cancel=None, opener=None, timeout=DOWNLOAD_TIMEOUT_SEC, version="dev",
                   public_key=None):
    """asset (parse_release の asset) を dest へ保存して検証する。失敗時は dest を残さない。
    SHA-256 (署名つきマニフェスト、または移行中は GitHub のハッシュ) が分からないときは、ダウンロードせずに中止する。"""
    url = asset.get("url", "") if isinstance(asset, dict) else ""
    if not url.lower().startswith("https://"):
        raise UpdateError("安全でないURL (https以外) からはダウンロードしません")
    opener = opener or _default_opener
    want_sha, want_size = expected_from_release(asset, opener=opener, timeout=timeout, version=version, public_key=public_key)
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneCam/%s" % version})
    part = dest
    try:
        with opener(req, timeout=timeout) as resp:
            final = getattr(resp, "geturl", lambda: url)()
            if isinstance(final, str) and not final.lower().startswith("https://"):
                raise UpdateError("安全でないURL (https以外) へ転送されたため中止しました")
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                total = 0
            total = total or int(want_size or 0)
            done = 0
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("キャンセルしました")
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        verify_file(part, want_size, want_sha)
    except UpdateError:
        _silent_remove(part)
        raise
    except Exception as e:
        _silent_remove(part)
        if isinstance(e, urllib.error.HTTPError):
            raise UpdateError("ダウンロード元が応答しませんでした (HTTP %s)" % e.code)
        raise UpdateError("ダウンロードに失敗しました。ネットワークを確認してください")
    return dest


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 入れ替え
# ---------------------------------------------------------------------------

class RealFS:
    exists = staticmethod(os.path.exists)
    rename = staticmethod(os.rename)     # 移動先があれば失敗する (Windows)
    remove = staticmethod(os.remove)
    listdir = staticmethod(os.listdir)


def dir_writable(folder, fs=None):
    """folder に新しいファイルを作れるか (Program Files などでは False)。"""
    probe = os.path.join(folder, ".phonecam_write_test_%d" % os.getpid())
    try:
        with open(probe, "wb") as f:
            f.write(b"x")
        os.remove(probe)
        return True
    except OSError:
        return False


def old_path_for(exe_path, fs=None):
    """PhoneCamReceiver.exe → PhoneCamReceiver.old.exe (残っていて消せなければ .1.old.exe …)。"""
    fs = fs or RealFS
    base = os.path.splitext(exe_path)[0]
    cand = base + OLD_SUFFIX
    n = 0
    while fs.exists(cand):
        try:
            fs.remove(cand)
            break
        except OSError:
            n += 1
            if n > 9:
                raise UpdateError("古いファイルを片付けられませんでした")
            cand = "%s.%d%s" % (base, n, OLD_SUFFIX)
    return cand


def self_replace(exe_path, new_path, fs=None):
    """動作中の exe を exe_path.old.exe へ改名し、new_path を元の名前へ移す。
    失敗したら元の名前へ戻して UpdateError。成功したら (old_path) を返す。"""
    fs = fs or RealFS
    old = old_path_for(exe_path, fs)
    try:
        fs.rename(exe_path, old)
    except OSError:
        raise UpdateError("実行中のファイルを入れ替えられませんでした")
    try:
        fs.rename(new_path, exe_path)
    except Exception:
        try:
            fs.rename(old, exe_path)
        except Exception:
            raise UpdateError("入れ替えに失敗し、元に戻すこともできませんでした。%s を %s へ名前変更してください" % (
                os.path.basename(old), os.path.basename(exe_path)))
        raise UpdateError("入れ替えに失敗したため、元のままにしました")
    return old


def undo_replace(exe_path, old_path, parked_path, fs=None):
    """新しい exe を起動できなかったとき: 新しい方を parked_path へ避け、古い方を元の名前へ戻す。"""
    fs = fs or RealFS
    try:
        fs.rename(exe_path, parked_path)
        fs.rename(old_path, exe_path)
        return True
    except Exception:
        return False


def cleanup_old(folder, fs=None, prefix="phonecam"):
    """前回の更新で残った <prefix>*.old.exe を消す (使用中などで消せないものは放置)。"""
    fs = fs or RealFS
    removed = []
    try:
        names = fs.listdir(folder)
    except OSError:
        return removed
    for name in names:
        if name.lower().endswith(OLD_SUFFIX) and name.lower().startswith(prefix.lower()):
            try:
                fs.remove(os.path.join(folder, name))
                removed.append(name)
            except OSError:
                pass
    return removed


def relaunch_env(env=None):
    """PyInstaller の onefile を子として起動するとき、親の展開先を引き継がせない。"""
    env = dict(os.environ if env is None else env)
    for k in list(env):
        if k.startswith("_PYI") or k == "_MEIPASS2":
            env.pop(k, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
