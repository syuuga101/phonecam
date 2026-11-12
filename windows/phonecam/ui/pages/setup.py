# -*- coding: utf-8 -*-
"""セットアップタブ (必要ソフトの導入・iPhone へのインストール・更新設定)
"""

import os
import time
from phonecam.core.deps import phonecam_audio, phonecam_setup
from phonecam.core.paths import _is_frozen
from phonecam.core.settings import save_settings
from phonecam.ui.dpi import px
from phonecam.ui.labels import UPD_BG, UPD_FG, UPD_FONT, UPD_LINE, UPD_SUB, setup_outcome_text
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.util import webbrowser_open


def _first_run_check(app):
    """起動後の確認 (exe 版のみ)。初回はセットアップウィザード、2回目以降は専用カメラの更新だけを案内する。"""
    if app._closing or not _is_frozen():
        return
    if app.setup is not None and not app.settings.get("setup_done"):
        if app._setup_key_present is False and app._refresh_cam_status() == "ok":
            # 旧バージョンから使っていて、専用カメラも登録済み: ウィザードは出さない
            app.settings["setup_done"] = True
            app._schedule_save()
            return
        app.open_setup_wizard()
        return
    if app.settings.get("backend") != "phonecam":
        return
    st = app._refresh_cam_status()
    if st not in ("missing", "outdated"):
        return
    if st == "outdated":
        msg = "新しいバージョンの専用カメラに更新します。このあと管理者の確認が表示されたら「はい」を押してください。"
    else:
        msg = "専用カメラ「PhoneCam」をWindowsに登録します。このあと管理者の確認が表示されたら「はい」を押してください。"
    if messagebox.askyesno("PhoneCam", msg):
        app._start_elevated("register")
    else:
        app.cam_note_var.set("専用カメラ「PhoneCam」は未登録（または古い）のままです。使うには「登録する（更新）」を押してください。"
                              "それまでは出力先に「OBS Virtual Camera」などを選べます。")


# -- セットアップ (必要ソフトの導入) -----------------------------------
def _setup_busy(app, flag):
    """セットアップ実行中は、専用カメラの登録ボタンなど管理者権限の操作を止める。"""
    app._elev_busy = bool(flag)
    try:
        app._refresh_cam_status()
    except Exception:
        pass


def _setup_changed(app):
    """検出結果が更新された。専用カメラの状態表示を合わせ、登録が変わっていれば出力を作り直す。"""
    try:
        app._refresh_cam_status()
        app.link.vcam.request_reset()
    except Exception:
        pass


def _build_setup_tab(app, parent):
    if app.setup is None:
        ttk.Label(parent, text="セットアップ機能を読み込めませんでした。").pack(padx=14, pady=14, anchor="w")
        return
    ps = phonecam_setup
    wrap = tk.Frame(parent, bg=ps.BG)
    wrap.pack(fill="both", expand=True, padx=10, pady=8)
    inner = tk.Frame(wrap, bg=ps.BG)
    inner.pack(fill="both", expand=True, padx=16, pady=14)
    tk.Label(inner, text="セットアップ", bg=ps.BG, fg=ps.FG, font=(ps.FONT, 18, "bold")).pack(anchor="w")
    app.setup_summary_var = tk.StringVar(value="確認しています…")
    tk.Label(inner, textvariable=app.setup_summary_var, bg=ps.BG, fg=ps.SUB, font=(ps.FONT, 10)).pack(anchor="w", pady=(4, 10))
    app.setup_panel = ps.SetupPanel(inner, app.setup, "tab", on_install_one=app._setup_install_one)
    app.setup_panel.pack(fill="x")
    bar = tk.Frame(inner, bg=ps.BG)
    bar.pack(fill="x", pady=(14, 0))
    app.setup_recheck_btn = ps.make_button(bar, "再チェック", app._setup_recheck)
    app.setup_recheck_btn.pack(side="left")
    app.setup_all_btn = ps.make_button(bar, "足りないものをすべてインストール", app._setup_install_missing, primary=True)
    app.setup_all_btn.pack(side="left", padx=(10, 0))
    app.setup_note_var = tk.StringVar(value="")
    tk.Label(inner, textvariable=app.setup_note_var, bg=ps.BG, fg=ps.WARN, font=(ps.FONT, 10), justify="left",
             wraplength=px(480)).pack(anchor="w", pady=(10, 0))
    app.setup_manual_box = tk.Frame(inner, bg=ps.BG)
    app.setup_manual_box.pack(anchor="w")
    tk.Frame(inner, bg=ps.LINE, height=1).pack(fill="x", pady=(14, 10))
    tk.Label(inner, text="iPhoneへのインストール", bg=ps.BG, fg=ps.FG, font=(ps.FONT, 11, "bold")).pack(anchor="w")
    row = tk.Frame(inner, bg=ps.BG)
    row.pack(anchor="w", pady=(8, 0))
    app.setup_iphone_btn = ps.make_button(row, "iPhoneのセットアップ", app._setup_iphone, primary=True)
    app.setup_iphone_btn.pack(side="left")
    row2 = tk.Frame(inner, bg=ps.BG)
    row2.pack(anchor="w", pady=(px(8), 0))
    app.setup_ipa_btn = ps.make_button(row2, "PhoneCam.ipaを開く", app._setup_open_ipa)
    if ps.ipa_url():
        app.setup_ipa_btn.pack(side="left")
    app.setup_alt_btn = ps.make_button(row2, "AltServerを起動", app._setup_launch_altserver)
    app.setup_alt_btn.pack(side="left", padx=(px(10), 0))
    app.setup_alt_btn.pack_forget()
    row3 = tk.Frame(inner, bg=ps.BG)
    row3.pack(anchor="w", pady=(px(8), 0))
    app.setup_diag_btn = ps.make_button(row3, "AltServerの診断", app._setup_diagnose)
    app.setup_diag_btn.pack(side="left")
    tk.Label(row3, text="AltStoreの更新で「エラー2003」などが出るとき", bg=ps.BG, fg=ps.SUB,
             font=(ps.FONT, 9)).pack(side="left", padx=(px(10), 0))
    try:
        app._build_update_section(inner, ps)
    except Exception:
        pass
    rerun = ps.make_link(inner, "初期セットアップをやり直す", app._setup_rerun)
    rerun.pack(anchor="w", pady=(16, 0))
    app.setup.listen(app._setup_event)
    app.setup_panel.render()
    app.root.after(400, app._setup_initial_check)


def _setup_initial_check(app):
    if app._closing or app.setup is None:
        return
    app._setup_last_check = time.monotonic()
    app.setup.recheck()


def _setup_event(app, what):
    """SetupController からの通知 (UI スレッド)。"""
    if app._closing or app.setup is None:
        return
    ps = phonecam_setup
    if what in ("statuses", "checking", "start", "done"):
        app.setup_panel.render()
        app._setup_update_summary()
    if what == "start":
        # ウィザードから始めたときは、ウィザード側に表示があるので、ここには「実行中」の文を出さない
        # (出すと、終わっても消えずに残る)
        app.setup_note_var.set("" if app._wizard is not None else
                                "インストール中です。管理者の確認が表示されたら「はい」を押してください…")
        app._setup_clear_manual()
    elif what == "stage" and app.setup.busy and app._wizard is None:
        app.setup_note_var.set(app.setup.stage)
    elif what == "done":
        app._setup_show_outcome()
    elif what == "audio":
        app._setup_show_audio()


def _setup_update_summary(app):
    ps = phonecam_setup
    c = app.setup
    if not c.checked:
        text = "確認しています…"
    else:
        missing = [x for x in ps.COMPONENTS if c.statuses.get(x["id"], {}).get("state") != ps.STATE_OK
                   and ps.is_selectable(x["id"], c.statuses)]
        text = "すべて導入済みです" if not missing else "未導入・要確認: %d 件" % len(missing)
    app.setup_summary_var.set(text)
    busy = c.busy
    for b in (app.setup_recheck_btn, app.setup_all_btn):
        try:
            b.config(state="disabled" if busy else "normal")
        except Exception:
            pass
    if ps.find_altserver():
        app.setup_alt_btn.pack(side="left", padx=(px(10), 0))
    else:
        app.setup_alt_btn.pack_forget()


def _setup_clear_manual(app):
    for w in list(app.setup_manual_box.winfo_children()):
        w.destroy()


def _setup_show_outcome(app):
    ps = phonecam_setup
    out = app.setup.outcome or {}
    app._setup_clear_manual()
    if app._wizard is not None:
        app.setup_note_var.set("")  # 結果はウィザードに表示される。「インストール中です」を残さない
        return
    app.setup_note_var.set(setup_outcome_text(out, lambda cid: ps.COMPONENT_BY_ID[cid]["name"]))
    for m in out.get("manual", []):
        ps.make_link(app.setup_manual_box, "%s の公式ページを開く（%s）" % (m["name"], m["text"]),
                     lambda u=m["url"]: webbrowser_open(u), bg=ps.BG).pack(anchor="w")


def _setup_show_audio(app):
    """VB-CABLE 導入後の音声デバイスの戻し結果 (完了のあとに届く) を、結果の下に足す。"""
    msg = getattr(app.setup, "audio_message", "")
    if not msg or app.setup.busy or app._wizard is not None:
        return
    cur = app.setup_note_var.get()
    if msg not in cur:
        app.setup_note_var.set((cur + "\n" if cur else "") + msg)


def _setup_recheck(app):
    if app.setup is not None:
        app.setup_note_var.set("")
        app._setup_clear_manual()
        app.setup.recheck()


def _setup_start(app, ids):
    """インストールを始める。iTunes / iCloud / AltServer / VB-CABLE は手順つきなので、案内の画面を開く。"""
    ps = phonecam_setup
    if app.setup is None:
        return
    _auto, guided = ps.split_ids(ps.expand_selection(ids, app.setup.statuses))
    if app.setup.start(ids) and guided and app._wizard is None:
        try:
            app._wizard = ps.SetupWizard(app.root, app.setup, on_finish=app._iphone_wizard_closed,
                                          on_skip=app._iphone_wizard_closed, open_url=webbrowser_open, start="guided")
            app.setup_note_var.set("")
        except Exception:
            app._wizard = None


def _setup_diagnose(app):
    phonecam_setup.open_diagnostics(app.root, open_url=webbrowser_open)


def _setup_install_one(app, cid):
    if app.setup is not None:
        app._setup_start([cid])


def _setup_install_missing(app):
    if app.setup is None:
        return
    ps = phonecam_setup
    ids = [x["id"] for x in ps.COMPONENTS
           if ps.is_selectable(x["id"], app.setup.statuses)
           and (x["required"] or x["id"] == "altserver")]
    if not ids:
        app.setup_note_var.set("インストールが必要なものはありません。")
        return
    app._setup_start(ids)


def _setup_open_ipa(app):
    url = phonecam_setup.ipa_url()
    if url:
        webbrowser_open(url)


def _setup_launch_altserver(app):
    path = phonecam_setup.find_altserver()
    if path:
        phonecam_setup._startfile(path)


def _setup_rerun(app):
    app.open_setup_wizard(rerun=True)


def _setup_iphone(app):
    """セットアップタブの「iPhoneのセットアップ」: iPhone の手順だけを開く。"""
    app.open_setup_wizard(iphone_only=True)


def _iphone_wizard_closed(app):
    app._wizard = None
    app._update_after_wizard()
    try:
        app.setup.recheck()
    except Exception:
        pass


# -- VB-CABLE 導入時の既定の音声デバイス保護 ------------------------------
def _audio_get(app, key):
    return app.settings.get(key)


def _audio_set(app, key, value):
    app.settings[key] = value
    save_settings(app.settings)


def _make_audio_guard(app):
    if phonecam_audio is None or os.name != "nt":
        return None
    try:
        return phonecam_audio.RestoreGuard(app._audio_get, app._audio_set)
    except Exception:
        return None


def _audio_startup_check(app):
    """起動時: VB-CABLE を入れた直後 (あと2回の起動まで) は、既定が CABLE に変わっていないか見て元に戻す。"""
    if app._closing or phonecam_audio is None:
        return
    guard = app._make_audio_guard()
    if guard is None or guard.pending() <= 0:
        return
    box = []
    phonecam_audio.run_in_thread(guard.startup_check, box.append)

    def poll():
        if app._closing:
            return
        if not box:
            app.root.after(500, poll)
            return
        res = box[0]
        if res and res.get("message"):
            try:
                app.setup_note_var.set(res["message"])
            except Exception:
                pass
            try:
                messagebox.showinfo("PhoneCam", res["message"])
            except Exception:
                pass

    app.root.after(500, poll)


def open_setup_wizard(app, rerun=False, iphone_only=False):
    if app.setup is None or app._wizard is not None or tk is None:
        return
    if app.setup.busy:
        return
    if iphone_only:
        try:
            app._wizard = phonecam_setup.SetupWizard(app.root, app.setup, on_finish=app._iphone_wizard_closed,
                                                      on_skip=app._iphone_wizard_closed, open_url=webbrowser_open,
                                                      start="iphone")
        except Exception:
            app._wizard = None
        return
    if rerun:
        app.settings["setup_done"] = False
        app._do_save_now()
    app.setup.touched.clear()
    app.setup.mic = False
    app.setup.checked = False
    app.setup.outcome = None
    app.setup_note_var.set("")
    try:
        app._wizard = phonecam_setup.SetupWizard(app.root, app.setup, on_finish=app._wizard_closed,
                                                  on_skip=app._wizard_closed, open_url=webbrowser_open)
    except Exception:
        app._wizard = None


def _wizard_closed(app):
    app._wizard = None
    app.settings["setup_done"] = True
    app._do_save_now()
    try:
        app.setup.recheck()
    except Exception:
        pass
    app._update_after_wizard()


def _build_update_section(app, parent, ps):
    """セットアップタブの「アップデート」欄。"""
    bg = ps.BG if ps is not None else UPD_BG
    fg = ps.FG if ps is not None else UPD_FG
    sub = ps.SUB if ps is not None else UPD_SUB
    font = ps.FONT if ps is not None else UPD_FONT
    tk.Frame(parent, bg=ps.LINE if ps is not None else UPD_LINE, height=1).pack(fill="x", pady=(14, 10))
    tk.Label(parent, text="アップデート", bg=bg, fg=fg, font=(font, 11, "bold")).pack(anchor="w")
    tk.Checkbutton(parent, text="起動時に更新を確認する", variable=app.upd_check_var, command=app._update_check_toggled,
                   bg=bg, activebackground=bg, fg=fg, activeforeground=fg, selectcolor="#ffffff",
                   font=(font, 10), anchor="w", bd=0, highlightthickness=0).pack(anchor="w", pady=(6, 0))
    row = tk.Frame(parent, bg=bg)
    row.pack(anchor="w", pady=(6, 0))
    app._upd_button(row, "今すぐ確認", app._update_check_now).pack(side="left")
    tk.Label(row, textvariable=app.upd_status_var, bg=bg, fg=sub, font=(font, 9), justify="left",
             wraplength=px(380), anchor="w").pack(side="left", padx=(px(10), 0))


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_setup_tab(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_first_run_check',
    '_setup_busy',
    '_setup_changed',
    '_build_setup_tab',
    '_setup_initial_check',
    '_setup_event',
    '_setup_update_summary',
    '_setup_clear_manual',
    '_setup_show_outcome',
    '_setup_show_audio',
    '_setup_recheck',
    '_setup_start',
    '_setup_diagnose',
    '_setup_install_one',
    '_setup_install_missing',
    '_setup_open_ipa',
    '_setup_launch_altserver',
    '_setup_rerun',
    '_setup_iphone',
    '_iphone_wizard_closed',
    '_audio_get',
    '_audio_set',
    '_make_audio_guard',
    '_audio_startup_check',
    'open_setup_wizard',
    '_wizard_closed',
    '_build_update_section',

)
