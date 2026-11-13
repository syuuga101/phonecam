# タスク: Qt版を配布できる状態に仕上げ、exe を Qt 版に切り替える

背景: 現在の配布版（Tkinter）はスクロール時に残像が出る。Qt 版（`windows/phonecam/qt/`、入口 `windows/phonecam_qt.py`）で全ページはそろったので、足りない部分を埋めて exe を Qt 版に切り替える。
読むもの: `docs/tasks/qt_contract.md`、`docs/tasks/ui_contract.md`、`docs/tasks/qt-pages.md`、現行 Tk 版（`windows/phonecam/ui/`）。

## 1. 自動アップデート（Tk 版と同じ動作）
- Tk 版 `windows/phonecam/ui/update_ui.py` と `windows/phonecam_update.py` のロジックを、UI 非依存部分は `controller.py`（または既存の `phonecam_update.py` の再利用）に寄せ、Qt で同じ体験を作る:
  起動時の自動確認（設定 `auto_update_check` 等、既存キーを使う）、上部の細い帯「新しいバージョン vX があります」＋「今すぐ更新」「更新内容を見る」「あとで」「このバージョンを通知しない」、設定ページの「更新を確認」、ダウンロード進捗、exe の差し替えと再起動（Tk 版の方式＝実行中の exe をリネームして置き換え をそのまま使う）。
- 更新内容（リリースノート）は QDialog で表示。

## 2. 上部の「iPhoneが熱い」警告帯
- Tk 版 `ui/app.py` の熱警告（`warn_text`）と同じ条件・文言で、`main_window.py` の上部帯に出す。色はパレット由来の控えめな警告色（背景を塗らず、左に小さな警告アイコン＋文字色でもよい）。

## 3. 小さな不具合
- 選択肢の文言が言語設定より先に作られ、英語と日本語が混ざる（例: 映像ページの「Wide」「Auto」「Follow orientation setting」）。`phonecam/ui/labels.py` は import 時に `t()` を評価している。Qt 版では、`QApplication` 作成前に設定の `language` を `i18n.set_language()` してから labels を import する、または labels を関数化して表示時に評価する。英語 Windows で言語「日本語」を選んだときに全部日本語になること。
- 左ナビの「音声」の右に小さな「-」が出る（スクショ参照: CI の qt-screens ブランチ `04_audio_*`）。原因を調べて消す。

## 4. exe を Qt 版に切り替える
- `.github/workflows/windows.yml` の PyInstaller を `phonecam_qt.py` 入口に変更（exe 名は `PhoneCamReceiver.exe` のまま）。PySide6 を `windows/requirements.txt` に追加（`PySide6==6.8.3`）。不要な Qt モジュールは除外してサイズを抑える（QtWebEngine、Qt3D、QtMultimedia、QtQml/Quick など使っていないもの）。`--collect-data` などで windows11 スタイル・SVG・アイコン用プラグイン（imageformats/qsvg、platforms/qwindows、styles）を確実に含める。
- 初期セットアップのウィザードは当面 Tk のまま（`--setup-wizard` / `--setup-diagnose` で別プロセス起動）。exe に tkinter / sv_ttk も残すこと。初回起動時・更新後の初回起動時にウィザードを出す既存の動作（`setup_done` / `setup_shown_version`）を Qt 版でも同じに。
- 管理者権限ヘルパー（`--register` / `--unregister` / `--setup <job>`）は今までどおり動くこと。
- ホットキー（`phonecam_hotkeys`）、OBS のカメラ設定ダイアログ連携（`phonecam_dshowctl`）、音声、仮想カメラなど、Tk 版で動いていた機能が Qt 版の exe でも動くこと（Controller 経由で起動されているか確認）。
- Tk の UI コード（`windows/phonecam/ui/`）は削除しない（ウィザードと互換テストで使う）。`phonecam_receiver.py` も残す。

## 確認
- `python -m py_compile` が通ること。
- Windows で `pip install -r windows/requirements.txt` → `python windows/phonecam_qt.py` で起動し、全ページを開いてスクロールしても残像が出ないこと、ライト/ダーク切替、200% 表示。
- `python windows/qt_smoke.py --out shots` が成功すること。
- 可能なら PyInstaller で exe を作って起動確認。
- PR の説明に、やったこと・確認したこと・残した課題を日本語で書く。
