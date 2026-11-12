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
from phonecam.ui.dpi import px
from phonecam.ui.labels import UPD_ACCENT, UPD_BG, UPD_BG_WHITE, UPD_FG, UPD_FONT, UPD_LINE, UPD_SUB, UPD_WARN
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.util import webbrowser_open


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
            app.upd_status_var.set("開発版のため、更新の確認はしません")
        return
    if auto and app._wizard is not None:
        app._upd_pending_wizard = True  # セットアップを閉じたあとに確認する
        return
    if app._upd_checking:
        return
    app._upd_checking = True
    app._upd_box = box = []
    if not auto:
        app.upd_status_var.set("確認しています…")

    def work():
        try:
            box.append(("ok", phonecam_update.fetch_latest(APP_REPO, APP_VERSION)))
        except phonecam_update.UpdateError as e:
            box.append(("err", str(e)))
        except Exception:
            box.append(("err", "確認できませんでした"))

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
            app.upd_status_var.set("最終確認: %s（v%s があります）" % (stamp, val["version"]))
        else:
            app.upd_status_var.set("最終確認: %s（最新です）" % stamp)
        if not auto:
            app._upd_dismissed.discard(val["tag"])
        app._update_refresh_banner(force=not auto)
    else:
        app.upd_status_var.set("最終確認: %s（確認できませんでした: %s）" % (stamp, val))


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
    app._upd_title_var.set("新しいバージョン v%s があります" % rel["version"])
    app._upd_sub_var.set("iPhoneアプリにも新しいバージョンがあります（AltStoreの「My Apps」で更新できます）" if ph_new else "")
    if ph_new:
        app._upd_sub.pack(anchor="w", pady=(2, 0), after=app._upd_title)
    else:
        app._upd_sub.pack_forget()
    app._upd_msg_var.set("")
    app._upd_progress_box.pack_forget()
    app._upd_btn_row.pack(anchor="w", pady=(8, 0))
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
        note = "iPhoneアプリとPCアプリのバージョンが違います（PC v%s, iPhone v%s）" % (
            phonecam_update.display_version(APP_VERSION), phonecam_update.display_version(ver))
    if app.ver_note_var.get() != note:
        app.ver_note_var.set(note)


# 更新の画面
def _upd_button(app, parent, text, command, kind="secondary"):
    if kind == "primary":
        return tk.Button(parent, text=text, command=command, bg=UPD_ACCENT, fg="#ffffff", activebackground="#0077ed",
                         activeforeground="#ffffff", relief="flat", bd=0, padx=px(16), pady=px(5), cursor="hand2",
                         font=(UPD_FONT, 10, "bold"))
    if kind == "link":
        return tk.Button(parent, text=text, command=command, bg=UPD_BG, fg=UPD_SUB, activebackground=UPD_BG,
                         activeforeground=UPD_FG, relief="flat", bd=0, padx=px(6), pady=px(5), cursor="hand2",
                         font=(UPD_FONT, 9))
    return tk.Button(parent, text=text, command=command, bg="#ffffff", fg=UPD_FG, activebackground="#ececf0",
                     activeforeground=UPD_FG, relief="flat", bd=0, padx=px(14), pady=px(5), cursor="hand2",
                     font=(UPD_FONT, 10))


def _build_update_banner(app, outer):
    bar = tk.Frame(outer, bg=UPD_BG)
    app._upd_banner = bar
    inner = tk.Frame(bar, bg=UPD_BG)
    inner.pack(fill="x", padx=px(16), pady=px(10))
    app._upd_title_var = tk.StringVar(value="")
    app._upd_sub_var = tk.StringVar(value="")
    app._upd_msg_var = tk.StringVar(value="")
    app._upd_title = tk.Label(inner, textvariable=app._upd_title_var, bg=UPD_BG, fg=UPD_FG,
                               font=(UPD_FONT, 11, "bold"), anchor="w")
    app._upd_title.pack(anchor="w")
    app._upd_sub = tk.Label(inner, textvariable=app._upd_sub_var, bg=UPD_BG, fg=UPD_SUB, font=(UPD_FONT, 9),
                             justify="left", wraplength=px(580), anchor="w")
    row = tk.Frame(inner, bg=UPD_BG)
    app._upd_btn_row = row
    app._upd_now_btn = app._upd_button(row, "今すぐ更新", app._update_now, "primary")
    app._upd_notes_btn = app._upd_button(row, "更新内容を見る", app._update_show_notes)
    app._upd_notes_btn.pack(side="left", padx=(px(8), 0))
    app._upd_button(row, "あとで", app._update_later, "link").pack(side="left", padx=(px(8), 0))
    app._upd_button(row, "このバージョンを通知しない", app._update_skip, "link").pack(side="left")
    row.pack(anchor="w", pady=(8, 0))
    prog = tk.Frame(inner, bg=UPD_BG)
    app._upd_progress_box = prog
    app._upd_prog_var = tk.StringVar(value="")
    tk.Label(prog, textvariable=app._upd_prog_var, bg=UPD_BG, fg=UPD_FG, font=(UPD_FONT, 9), anchor="w").pack(anchor="w")
    pr = tk.Frame(prog, bg=UPD_BG)
    pr.pack(fill="x", pady=(4, 0))
    app._upd_bar = ttk.Progressbar(pr, orient="horizontal", mode="determinate", maximum=100)
    app._upd_bar.pack(side="left", fill="x", expand=True)
    app._upd_button(pr, "キャンセル", app._update_cancel, "link").pack(side="left", padx=(px(8), 0))
    tk.Label(inner, textvariable=app._upd_msg_var, bg=UPD_BG, fg=UPD_WARN, font=(UPD_FONT, 9), justify="left",
             wraplength=px(580), anchor="w").pack(anchor="w", pady=(4, 0))
    tk.Frame(bar, bg=UPD_LINE, height=1).pack(fill="x", side="bottom")


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
    win.title("更新内容 v%s" % rel["version"])
    win.configure(bg=UPD_BG_WHITE)
    win.minsize(px(420), px(300))
    win.geometry("%dx%d" % (px(520), px(420)))
    try:
        win.transient(app.root)
    except Exception:
        pass
    head = tk.Frame(win, bg=UPD_BG_WHITE)
    head.pack(fill="x", padx=px(18), pady=(px(16), px(6)))
    tk.Label(head, text="v%s の更新内容" % rel["version"], bg=UPD_BG_WHITE, fg=UPD_FG,
             font=(UPD_FONT, 14, "bold")).pack(anchor="w")
    foot = tk.Frame(win, bg=UPD_BG_WHITE)
    foot.pack(side="bottom", fill="x", padx=px(18), pady=px(14))
    app._upd_button(foot, "リリースページを開く", app._update_open_page, "primary").pack(side="left")
    app._upd_button(foot, "閉じる", win.destroy).pack(side="left", padx=(px(8), 0))
    mid = tk.Frame(win, bg=UPD_BG_WHITE)
    mid.pack(fill="both", expand=True, padx=px(18))
    txt = tk.Text(mid, wrap="word", bg=UPD_BG, fg=UPD_FG, relief="flat", bd=0, padx=px(12), pady=px(10),
                  font=(UPD_FONT, 10), height=8)
    sb = ttk.Scrollbar(mid, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    body = phonecam_update.markdown_to_text(rel.get("body", "")) or "更新内容は書かれていません。"
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
            app._upd_msg_var.set("このリリースには自動更新用のファイルがないため、ダウンロードページを開きました。")
        return
    exe = os.path.abspath(sys.executable)
    folder = os.path.dirname(exe)
    if not phonecam_update.dir_writable(folder):
        app._update_open_page()
        app._upd_msg_var.set("このフォルダには書き込めないため自動で更新できません。開いたページから PhoneCamReceiver.exe を"
                              "ダウンロードして置き換えてください。")
        return
    if app.state.snapshot()["connected"]:
        if not messagebox.askyesno("PhoneCam", "更新するとアプリが再起動し、接続中の映像はいったん切れます。更新しますか？"):
            return
    dest = os.path.splitext(exe)[0] + ".download"
    cancel = threading.Event()
    dl = {"done": 0, "total": int(asset.get("size") or 0), "cancel": cancel, "box": [], "dest": dest, "exe": exe}
    app._upd_dl = dl
    app._upd_msg_var.set("")
    app._upd_btn_row.pack_forget()
    app._upd_progress_box.pack(fill="x", pady=(8, 0))
    app._upd_bar.configure(value=0)
    app._upd_prog_var.set("ダウンロードしています…")

    def progress(done, total):
        dl["done"], dl["total"] = done, total or dl["total"]

    def work():
        try:
            phonecam_update.download_asset(asset, dest, progress=progress, cancel=cancel, version=APP_VERSION)
            dl["box"].append(("ok", None))
        except phonecam_update.UpdateError as e:
            dl["box"].append(("err", str(e)))
        except Exception:
            dl["box"].append(("err", "ダウンロードに失敗しました"))

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
            app._upd_prog_var.set("ダウンロードしています… %d%%（%s / %s）" % (
                min(100, int(done * 100 / total)), format_bytes(done), format_bytes(total)))
        else:
            app._upd_prog_var.set("ダウンロードしています… %s" % format_bytes(done))
        app.root.after(150, app._update_dl_poll)
        return
    kind, msg = dl["box"][0]
    if kind != "ok":
        app._update_fail(msg)
        return
    app._upd_prog_var.set("入れ替えています…")
    app._update_apply(dl)


def _update_fail(app, msg):
    dl, app._upd_dl = app._upd_dl, None
    if dl:
        try:
            os.remove(dl["dest"])
        except OSError:
            pass
    app._upd_progress_box.pack_forget()
    app._upd_btn_row.pack(anchor="w", pady=(8, 0))
    app._upd_msg_var.set("更新できませんでした: %s" % msg)
    app._update_refresh_banner(force=True)
    app._upd_msg_var.set("更新できませんでした: %s" % msg)


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
        app._update_fail("新しいバージョンを起動できませんでした%s (%s)" % ("。元のままにしました" if ok else "", e))
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
