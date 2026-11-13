# -*- coding: utf-8 -*-
"""更新確認・更新バナー・自己更新の UI 側 (phonecam_update との橋渡し)
"""

import os
import subprocess
import sys
import threading
import time
from phonecam.core.deps import phonecam_update
from phonecam.core.paths import _app_dir, _is_frozen
from phonecam.core.stats import format_bytes
from phonecam.core.version import APP_REPO, APP_VERSION
from phonecam.i18n import t
from phonecam.ui.dpi import px
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.util import webbrowser_open

try:  # 共通部品 (外枠担当)。まだ無い間は簡易版で代用する (settings.py / setup.py もここから取る)
    from phonecam.ui.widgets import hint, page, row, section
except ImportError:  # pragma: no cover - 共通部品が入れば使われない
    def page(parent):
        f = ttk.Frame(parent)
        f.pack(fill="both", expand=True, padx=24, pady=12)
        return f

    def section(parent, title):
        ttk.Label(parent, text=title).pack(anchor="w", pady=(12, 4))
        card = ttk.Frame(parent, padding=12)
        card.pack(fill="x")
        return card

    def hint(parent, text):
        lbl = ttk.Label(parent, text=text)
        lbl.pack(anchor="w")
        return lbl

    def row(card, icon, title, hint=None):
        f = ttk.Frame(card)
        f.pack(fill="x", pady=4)
        left = ttk.Frame(f)
        left.pack(side="left")
        ttk.Label(left, text=title).pack(anchor="w")
        if hint:
            ttk.Label(left, text=hint).pack(anchor="w")
        return f


def _theme_colors():
    """色は theme から取る (共通部品が無い間は None)。"""
    try:
        from phonecam.ui import theme
        return theme.colors()
    except Exception:
        return None


def _version_text():
    try:
        return phonecam_update.display_version(APP_VERSION) if phonecam_update is not None else APP_VERSION
    except Exception:
        return APP_VERSION


# -- 更新の確認と自己更新 -------------------------------------------------
def _update_possible(app):
    """更新確認ができるか (開発版・確認先なしでは確認しない)。"""
    return (phonecam_update is not None and bool(APP_REPO)
            and phonecam_update.parse_version(APP_VERSION) is not None)


def _update_init(app):
    if phonecam_update is None:
        return
    if _is_frozen():
        try:
            stem = os.path.splitext(os.path.basename(sys.executable))[0]
            phonecam_update.cleanup_old(_app_dir(), prefix=stem)  # 前回の更新で残った古い exe を消す
        except Exception:
            pass
    app.root.after(3000, app._update_startup)


def _update_startup(app):
    if app._closing:
        return
    app.root.after(int(phonecam_update.CHECK_INTERVAL_SEC * 1000), app._update_periodic)
    app._update_try(auto=True)


def _update_periodic(app):
    if app._closing:
        return
    app.root.after(int(phonecam_update.CHECK_INTERVAL_SEC * 1000), app._update_periodic)
    app._update_try(auto=True)


def _update_after_wizard(app):
    if app._upd_pending_wizard:
        app._upd_pending_wizard = False
        app.root.after(1500, lambda: app._update_try(auto=True))


def _update_check_toggled(app):
    app.settings["update_check"] = bool(app.upd_check_var.get())
    app._schedule_save()


def _update_check_now(app):
    app._update_try(auto=False)


def _update_try(app, auto):
    if app._closing:
        return
    if auto and not app.settings.get("update_check", True):
        return
    if not app._update_possible():
        if not auto:
            app.upd_status_var.set(t("update.status.dev"))
        return
    if auto and app._wizard is not None:
        app._upd_pending_wizard = True  # セットアップを閉じたあとに確認する
        return
    if app._upd_checking:
        return
    app._upd_checking = True
    app._upd_box = box = []
    if not auto:
        app.upd_status_var.set(t("update.status.checking"))

    def work():
        try:
            box.append(("ok", phonecam_update.fetch_latest(APP_REPO, APP_VERSION)))
        except phonecam_update.UpdateError as e:
            box.append(("err", str(e)))
        except Exception:
            box.append(("err", t("update.status.failed_short")))

    threading.Thread(target=work, name="update-check", daemon=True).start()
    app.root.after(200, lambda: app._update_poll(box, auto))


def _update_poll(app, box, auto):
    if app._closing:
        return
    if not box:
        app.root.after(200, lambda: app._update_poll(box, auto))
        return
    app._upd_checking = False
    kind, val = box[0]
    stamp = time.strftime("%m/%d %H:%M")
    if kind == "ok":
        app._upd_release = val
        if phonecam_update.is_newer(val["tag"], APP_VERSION):
            app.upd_status_var.set(t("update.status.available", time=stamp, version=val["version"]))
        else:
            app.upd_status_var.set(t("update.status.latest", time=stamp))
        if not auto:
            app._upd_dismissed.discard(val["tag"])
        app._update_refresh_banner(force=not auto)
    else:
        app.upd_status_var.set(t("update.status.error", time=stamp, error=val))


def _update_banner_state(app, force=False):
    """(表示するか, PCが新しいか, iPhoneが古いか)。"""
    rel = app._upd_release
    if not rel:
        return False, False, False
    tag = rel["tag"]
    pc_new = phonecam_update.is_newer(tag, APP_VERSION)
    ph_new = bool(app._phone_ver) and phonecam_update.is_newer(tag, app._phone_ver)
    if not (pc_new or ph_new):
        return False, pc_new, ph_new
    if tag in app._upd_dismissed:
        return False, pc_new, ph_new
    if not force and not phonecam_update.skip_allows(tag, app.settings.get("update_skip_version", "")):
        return False, pc_new, ph_new
    return True, pc_new, ph_new


def _update_refresh_banner(app, force=False):
    banner = getattr(app, "_upd_banner", None)
    if banner is None or app._closing:
        return
    if app._upd_dl is not None:
        return  # ダウンロード中は表示を変えない
    show, pc_new, ph_new = app._update_banner_state(force)
    if not show:
        app._update_hide_banner()
        return
    rel = app._upd_release
    app._upd_title_var.set(t("update.banner.title", version=rel["version"]))
    app._upd_sub_var.set(t("update.banner.phone_new") if ph_new else "")
    if ph_new:
        app._upd_sub.pack(anchor="w", pady=(2, 0))
    else:
        app._upd_sub.pack_forget()
    app._upd_msg_var.set("")
    app._upd_progress_box.pack_forget()
    app._upd_btn_row.pack(side="right")
    if pc_new:
        app._upd_now_btn.pack(side="left", before=app._upd_notes_btn)
    else:
        app._upd_now_btn.pack_forget()
    if not banner.winfo_ismapped():
        banner.pack(side="top", fill="x", before=app.nb)


def _update_hide_banner(app):
    try:
        app._upd_banner.pack_forget()
    except Exception:
        pass


def _update_note_phone_version(app, ver):
    """iPhone から届いた appVersion を覚え、PC と major.minor が違えば状態タブに控えめに知らせる。"""
    ver = ver if isinstance(ver, str) else ""
    if phonecam_update is None:
        return
    if ver and ver != app._phone_ver:
        app._phone_ver = ver
        if app._upd_release is not None:
            app._update_refresh_banner()
    note = ""
    if (ver and phonecam_update.parse_version(ver) is not None
            and phonecam_update.parse_version(APP_VERSION) is not None
            and not phonecam_update.same_major_minor(ver, APP_VERSION)):
        note = t("update.note.version_mismatch", pc=phonecam_update.display_version(APP_VERSION),
                 iphone=phonecam_update.display_version(ver))
    if app.ver_note_var.get() != note:
        app.ver_note_var.set(note)


def _build_update_rows(app, card):
    """設定ページ・セットアップページ共通の「更新」行 (現在のバージョン / 更新を確認 / 自動確認)。"""
    r = row(card, "info", t("update.version.label"))
    ttk.Label(r, text=_version_text()).pack(side="right")
    r = row(card, "download", t("update.check.label"))
    app._upd_button(r, t("update.check.button"), app._update_check_now).pack(side="right")
    ttk.Label(r, textvariable=app.upd_status_var).pack(side="right", padx=(0, px(10)))
    r = row(card, "clock", t("update.auto.label"))
    ttk.Checkbutton(r, variable=app.upd_check_var, command=app._update_check_toggled,
                    style="Switch.TCheckbutton").pack(side="right")


# 更新の画面
def _upd_button(app, parent, text, command, kind="secondary"):
    """ttk ボタン (見た目は sv-ttk のテーマに任せる)。primary は Accent、link は Toolbutton。"""
    style = {"primary": "Accent.TButton", "link": "Toolbutton"}.get(kind, "TButton")
    try:
        return ttk.Button(parent, text=text, command=command, style=style)
    except Exception:
        return ttk.Button(parent, text=text, command=command)


def _build_update_banner(app, outer):
    """更新のお知らせ: ウィンドウ上部の細い帯 (1行: 文言 | ボタン)。"""
    bar = ttk.Frame(outer)
    app._upd_banner = bar
    inner = ttk.Frame(bar)
    inner.pack(fill="x", padx=px(16), pady=px(6))
    app._upd_title_var = tk.StringVar(value="")
    app._upd_sub_var = tk.StringVar(value="")
    app._upd_msg_var = tk.StringVar(value="")
    top = ttk.Frame(inner)
    top.pack(fill="x")
    row = ttk.Frame(top)
    app._upd_btn_row = row
    app._upd_title = ttk.Label(top, textvariable=app._upd_title_var, anchor="w")
    app._upd_title.pack(side="left")
    app._upd_now_btn = app._upd_button(row, t("update.banner.now"), app._update_now, "primary")
    app._upd_notes_btn = app._upd_button(row, t("update.banner.notes"), app._update_show_notes)
    app._upd_notes_btn.pack(side="left", padx=(px(8), 0))
    app._upd_button(row, t("update.banner.later"), app._update_later, "link").pack(side="left", padx=(px(8), 0))
    app._upd_button(row, t("update.banner.skip"), app._update_skip, "link").pack(side="left")
    row.pack(side="right")
    app._upd_sub = ttk.Label(inner, textvariable=app._upd_sub_var, justify="left",
                              wraplength=px(580), anchor="w")
    prog = ttk.Frame(inner)
    app._upd_progress_box = prog
    app._upd_prog_var = tk.StringVar(value="")
    ttk.Label(prog, textvariable=app._upd_prog_var, anchor="w").pack(anchor="w")
    pr = ttk.Frame(prog)
    pr.pack(fill="x", pady=(4, 0))
    app._upd_bar = ttk.Progressbar(pr, orient="horizontal", mode="determinate", maximum=100)
    app._upd_bar.pack(side="left", fill="x", expand=True)
    app._upd_button(pr, t("update.progress.cancel"), app._update_cancel, "link").pack(side="left", padx=(px(8), 0))
    ttk.Label(inner, textvariable=app._upd_msg_var, justify="left",
              wraplength=px(580), anchor="w").pack(anchor="w", pady=(2, 0))
    ttk.Separator(bar, orient="horizontal").pack(fill="x", side="bottom")


def _update_later(app):
    if app._upd_release:
        app._upd_dismissed.add(app._upd_release["tag"])
    app._update_hide_banner()


def _update_skip(app):
    if app._upd_release:
        app.settings["update_skip_version"] = app._upd_release["version"]
        app._do_save_now()
    app._update_hide_banner()


def _update_open_page(app):
    rel = app._upd_release or {}
    url = rel.get("html_url") or ("https://github.com/%s/releases/latest" % APP_REPO if APP_REPO else "")
    if url:
        webbrowser_open(url)


def _update_show_notes(app):
    rel = app._upd_release
    if not rel:
        return
    win = app._upd_notes_win
    try:
        if win is not None and win.winfo_exists():
            win.destroy()
    except Exception:
        pass
    win = tk.Toplevel(app.root)
    app._upd_notes_win = win
    win.title(t("update.notes.window_title", version=rel["version"]))
    win.minsize(px(420), px(300))
    win.geometry("%dx%d" % (px(520), px(420)))
    try:
        win.transient(app.root)
    except Exception:
        pass
    col = _theme_colors() or {}
    outer = ttk.Frame(win)
    outer.pack(fill="both", expand=True)
    head = ttk.Frame(outer)
    head.pack(fill="x", padx=px(18), pady=(px(16), px(6)))
    ttk.Label(head, text=t("update.notes.heading", version=rel["version"]),
              font=("Segoe UI Semibold", 14)).pack(anchor="w")
    foot = ttk.Frame(outer)
    foot.pack(side="bottom", fill="x", padx=px(18), pady=px(14))
    app._upd_button(foot, t("update.notes.open_release"), app._update_open_page, "primary").pack(side="left")
    app._upd_button(foot, t("update.notes.close"), win.destroy).pack(side="left", padx=(px(8), 0))
    mid = ttk.Frame(outer)
    mid.pack(fill="both", expand=True, padx=px(18))
    txt = tk.Text(mid, wrap="word", relief="flat", bd=0, padx=px(12), pady=px(10),
                  font=("Segoe UI", 10), height=8,
                  **({"bg": col["card"], "fg": col["text"]} if col.get("card") and col.get("text") else {}))
    sb = ttk.Scrollbar(mid, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    body = phonecam_update.markdown_to_text(rel.get("body", "")) or t("update.notes.empty")
    txt.insert("1.0", body)
    txt.configure(state="disabled")


def _update_now(app):
    rel = app._upd_release
    if not rel or app._upd_dl is not None:
        return
    asset = rel.get("asset")
    if not _is_frozen() or not asset:
        app._update_open_page()
        if _is_frozen():
            app._upd_msg_var.set(t("update.msg.no_asset"))
        return
    exe = os.path.abspath(sys.executable)
    folder = os.path.dirname(exe)
    if not phonecam_update.dir_writable(folder):
        app._update_open_page()
        app._upd_msg_var.set(t("update.msg.not_writable"))
        return
    if app.state.snapshot()["connected"]:
        if not messagebox.askyesno("PhoneCam", t("update.confirm.restart")):
            return
    dest = os.path.splitext(exe)[0] + ".download"
    cancel = threading.Event()
    dl = {"done": 0, "total": int(asset.get("size") or 0), "cancel": cancel, "box": [], "dest": dest, "exe": exe}
    app._upd_dl = dl
    app._upd_msg_var.set("")
    app._upd_btn_row.pack_forget()
    app._upd_progress_box.pack(fill="x", pady=(8, 0))
    app._upd_bar.configure(value=0)
    app._upd_prog_var.set(t("update.progress.downloading"))

    def progress(done, total):
        dl["done"], dl["total"] = done, total or dl["total"]

    def work():
        try:
            phonecam_update.download_asset(asset, dest, progress=progress, cancel=cancel, version=APP_VERSION)
            dl["box"].append(("ok", None))
        except phonecam_update.UpdateError as e:
            dl["box"].append(("err", str(e)))
        except Exception:
            dl["box"].append(("err", t("update.msg.download_failed")))

    threading.Thread(target=work, name="update-download", daemon=True).start()
    app.root.after(150, app._update_dl_poll)


def _update_cancel(app):
    if app._upd_dl is not None:
        app._upd_dl["cancel"].set()


def _update_dl_poll(app):
    dl = app._upd_dl
    if dl is None or app._closing:
        return
    if not dl["box"]:
        done, total = dl["done"], dl["total"]
        if total > 0:
            app._upd_bar.configure(value=min(100, done * 100.0 / total))
            app._upd_prog_var.set(t("update.progress.percent", percent=min(100, int(done * 100 / total)),
                                          done=format_bytes(done), total=format_bytes(total)))
        else:
            app._upd_prog_var.set(t("update.progress.bytes", done=format_bytes(done)))
        app.root.after(150, app._update_dl_poll)
        return
    kind, msg = dl["box"][0]
    if kind != "ok":
        app._update_fail(msg)
        return
    app._upd_prog_var.set(t("update.progress.replacing"))
    app._update_apply(dl)


def _update_fail(app, msg):
    dl, app._upd_dl = app._upd_dl, None
    if dl:
        try:
            os.remove(dl["dest"])
        except OSError:
            pass
    app._upd_progress_box.pack_forget()
    app._upd_btn_row.pack(side="right")
    app._upd_msg_var.set(t("update.msg.failed", error=msg))
    app._update_refresh_banner(force=True)
    app._upd_msg_var.set(t("update.msg.failed", error=msg))


def _update_apply(app, dl):
    exe, new = dl["exe"], dl["dest"]
    try:
        old = phonecam_update.self_replace(exe, new)
    except phonecam_update.UpdateError as e:
        app._update_fail(str(e))
        return
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([exe] + sys.argv[1:], close_fds=True, cwd=os.path.dirname(exe),
                         env=phonecam_update.relaunch_env(), **kw)
    except Exception as e:
        parked = os.path.splitext(exe)[0] + ".failed.exe"
        ok = phonecam_update.undo_replace(exe, old, parked)
        app._upd_dl = None
        app._update_fail(t("update.msg.relaunch_failed_restored" if ok else "update.msg.relaunch_failed", error=e))
        return
    app._upd_dl = None
    app.on_close()


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_update_possible',
    '_update_init',
    '_update_startup',
    '_update_periodic',
    '_update_after_wizard',
    '_update_check_toggled',
    '_update_check_now',
    '_update_try',
    '_update_poll',
    '_update_banner_state',
    '_update_refresh_banner',
    '_update_hide_banner',
    '_update_note_phone_version',
    '_upd_button',
    '_build_update_banner',
    '_build_update_rows',
    '_update_later',
    '_update_skip',
    '_update_open_page',
    '_update_show_notes',
    '_update_now',
    '_update_cancel',
    '_update_dl_poll',
    '_update_fail',
    '_update_apply',

)
