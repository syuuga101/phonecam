# -*- coding: utf-8 -*-
"""PhoneCam 受信アプリの更新確認と自己更新 (GUI なしの部分)。

- GitHub Releases の最新版を確認する
- PhoneCamReceiver.exe をダウンロードして検証する (https のみ・サイズ・SHA-256)
- 動いている exe を「名前変更 → 新しいファイルを元の名前へ」で入れ替える
画面は phonecam/qt 側。ここは純粋な関数とクラスだけにしてテストしやすくしている。
"""

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
    if isinstance(assets, list):
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
            asset = {"name": ASSET_NAME, "url": url, "size": size, "sha256": sha256}
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
    """サイズ・SHA-256 (分かるものだけ)・exe の先頭 (MZ) を確かめる。"""
    try:
        actual = os.path.getsize(path)
    except OSError:
        raise UpdateError("ダウンロードしたファイルが見つかりません")
    if actual < MIN_EXE_BYTES:
        raise UpdateError("ダウンロードしたファイルが小さすぎます")
    if size and actual != size:
        raise UpdateError("ダウンロードが途中で切れました（サイズが一致しません）")
    if sha256 and sha256_file(path).lower() != sha256.lower():
        raise UpdateError("ダウンロードしたファイルが公開されたものと一致しません（SHA-256の不一致）。安全のため更新しません")
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise UpdateError("ダウンロードしたファイルの形式が正しくありません")


def download_asset(asset, dest, progress=None, cancel=None, opener=None, timeout=DOWNLOAD_TIMEOUT_SEC, version="dev"):
    """asset (parse_release の asset) を dest へ保存して検証する。失敗時は dest を残さない。"""
    url = asset.get("url", "") if isinstance(asset, dict) else ""
    if not url.lower().startswith("https://"):
        raise UpdateError("安全でないURL (https以外) からはダウンロードしません")
    opener = opener or _default_opener
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
            total = total or int(asset.get("size") or 0)
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
        verify_file(part, asset.get("size") or 0, asset.get("sha256") or "")
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
