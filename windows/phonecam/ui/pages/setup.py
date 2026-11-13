# -*- coding: utf-8 -*-
"""セットアップタブ (必要ソフトの導入・iPhone へのインストール・更新設定)
"""

import os
import time
from phonecam.core.deps import phonecam_audio, phonecam_setup, phonecam_update
from phonecam.core.paths import _is_frozen
from phonecam.core.settings import save_settings
from phonecam.core.version import APP_VERSION
from phonecam.i18n import t
from phonecam.ui.dpi import px
from phonecam.ui.labels import setup_outcome_text
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.update_ui import hint, page, row, section
from phonecam.ui.util import webbrowser_open


def _update_wizard_version():
    """更新後にウィザードを出す対象の版 (表示用)。dev などの実バージョンでないビルドは None。"""
    try:
        pv = phonecam_update.parse_version(APP_VERSION) if phonecam_update is not None else None
        if pv is None or pv == (0, 0, 0):
            return None
        return phonecam_update.display_version(APP_VERSION)
    except Exception:
        return None


def _first_run_check(app):
    """起動後の確認 (exe 版のみ)。初回はセットアップウィザード、2回目以降は専用カメラの更新だけを案内する。"""
    if app._closing or not _is_frozen():
        return
    cur_ver = _update_wizard_version()
    if app.setup is not None and not app.settings.get("setup_done"):
        if app._setup_key_present is False and app._refresh_cam_status() == "ok":
            # 旧バージョンから使っていて、専用カメラも登録済み: ウィザードは出さない
            app.settings["setup_done"] = True
            if cur_ver:
                app.settings["setup_shown_version"] = cur_ver
            app._schedule_save()
            return
        if cur_ver:
            app.settings["setup_shown_version"] = cur_ver  # 初回ウィザード: この版では更新後の再表示をしない
        app.open_setup_wizard()
        return
    if app.setup is not None and cur_ver and app.settings.get("setup_shown_version", "") != cur_ver:
        # 更新後の初回起動: 同じウィザードをもう一度 (導入済みの項目は「済み」と出る)。閉じても毎回は出さないよう、表示時点で保存する
        app.settings["setup_shown_version"] = cur_ver
        app._do_save_now()
        app.open_setup_wizard(after_update=cur_ver)
        return
    if app.settings.get("backend") != "phonecam":
        return
    st = app._refresh_cam_status()
    if st not in ("missing", "outdated"):
        return
    if st == "outdated":
        msg = t("setup.first_run.update_cam")
    else:
        msg = t("setup.first_run.register_cam")
    if messagebox.askyesno("PhoneCam", msg):
        app._start_elevated("register")
    else:
        app.cam_note_var.set(t("setup.first_run.declined_note"))


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
    body = page(parent)
    if app.setup is None:
        ttk.Label(body, text=t("setup.unavailable")).pack(padx=14, pady=14, anchor="w")
        return
    ps = phonecam_setup
    # 必要なソフト: 状況の要約 + 項目の一覧 (SetupPanel) + 操作
    card = section(body, t("setup.components.title"))
    app.setup_summary_var = tk.StringVar(value=t("setup.summary.checking"))
    ttk.Label(card, textvariable=app.setup_summary_var).pack(anchor="w", pady=(0, px(6)))
    app.setup_panel = ps.SetupPanel(card, app.setup, "tab", on_install_one=app._setup_install_one)
    app.setup_panel.pack(fill="x")
    bar = ttk.Frame(card)
    bar.pack(fill="x", pady=(px(10), 0))
    app.setup_recheck_btn = ttk.Button(bar, text=t("setup.recheck"), command=app._setup_recheck)
    app.setup_recheck_btn.pack(side="left")
    app.setup_all_btn = ttk.Button(bar, text=t("setup.install_missing"), command=app._setup_install_missing,
                                   style="Accent.TButton")
    app.setup_all_btn.pack(side="left", padx=(px(10), 0))
    app.setup_note_var = tk.StringVar(value="")
    ttk.Label(card, textvariable=app.setup_note_var, justify="left", wraplength=px(480)).pack(anchor="w", pady=(px(10), 0))
    app.setup_manual_box = ttk.Frame(card)
    app.setup_manual_box.pack(anchor="w")
    # iPhone へのインストール
    card = section(body, t("setup.iphone.title"))
    r = row(card, "download", t("setup.iphone.setup.label"), t("setup.iphone.setup.hint"))
    app.setup_iphone_btn = ttk.Button(r, text=t("setup.iphone.setup.button"), command=app._setup_iphone,
                                      style="Accent.TButton")
    app.setup_iphone_btn.pack(side="right")
    r2 = row(card, "document", t("setup.iphone.files.label"))
    # AltServer は見つかったときだけ _setup_update_summary が出す (ipa の左に並ぶ)
    app.setup_alt_btn = ttk.Button(r2, text=t("setup.iphone.altserver"), command=app._setup_launch_altserver)
    app.setup_ipa_btn = ttk.Button(r2, text=t("setup.iphone.ipa"), command=app._setup_open_ipa)
    if ps.ipa_url():
        app.setup_ipa_btn.pack(side="right")
    app._setup_files_row = r2
    r3 = row(card, "wrench", t("setup.diagnose.label"), t("setup.diagnose.hint"))
    app.setup_diag_btn = ttk.Button(r3, text=t("setup.diagnose.button"), command=app._setup_diagnose)
    app.setup_diag_btn.pack(side="right")
    try:
        app._build_update_section(body, ps)
    except Exception:
        pass
    r4 = row(section(body, t("setup.rerun.title")), "play", t("setup.rerun.label"))
    ttk.Button(r4, text=t("setup.rerun.button"), command=app._setup_rerun).pack(side="right")
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
                                t("setup.note.installing"))
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
        text = t("setup.summary.checking")
    else:
        missing = [x for x in ps.COMPONENTS if c.statuses.get(x["id"], {}).get("state") != ps.STATE_OK
                   and ps.is_selectable(x["id"], c.statuses)]
        text = t("setup.summary.all_ok") if not missing else t("setup.summary.missing", count=len(missing))
    app.setup_summary_var.set(text)
    busy = c.busy
    for b in (app.setup_recheck_btn, app.setup_all_btn):
        try:
            b.config(state="disabled" if busy else "normal")
        except Exception:
            pass
    if ps.find_altserver():
        app.setup_alt_btn.pack(side="right", padx=(px(10), 0))
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
        ttk.Button(app.setup_manual_box, text=t("setup.manual.open", name=m["name"], text=m["text"]),
                   command=lambda u=m["url"]: webbrowser_open(u), style="Toolbutton").pack(anchor="w")


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
        app.setup_note_var.set(t("setup.note.nothing_to_install"))
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


def open_setup_wizard(app, rerun=False, iphone_only=False, after_update=None):
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
                                                  on_skip=app._wizard_closed, open_url=webbrowser_open,
                                                  **({"update_version": after_update} if after_update else {}))
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
    """セットアップページの「アップデート」欄 (設定ページと同じ行)。"""
    card = section(parent, t("update.section"))
    app._build_update_rows(card)


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
