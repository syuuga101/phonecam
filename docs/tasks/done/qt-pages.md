# タスク: Qt版の残り8ページを実装する

PhoneCam の Windows 受信アプリを Tkinter から PySide6 (Qt 6.8) へ移行中。土台（`windows/phonecam/controller.py`、`windows/phonecam/qt/` の外枠・部品・接続ページ）はできている。残りのページを実装してほしい。

## 読むもの
- `docs/tasks/qt_contract.md` — Controller と `phonecam.qt.widgets` の API（必ずこれを使う）
- `docs/tasks/ui_contract.md` — 見た目のルール（Windows 11 設定アプリ風・飾りなし・説明文は最小）
- 手本: `windows/phonecam/qt/pages/connection.py`（完成済みのページ）
- 機能の正解: 現行 Tk 版 `windows/phonecam/ui/pages/*.py`（ここにある項目・動作をすべて Qt 版へ移す。勝手に機能を増やさない・減らさない）

## 実装するページ（各ファイルの `build(ctrl) -> QWidget` を中身ありにする）
| Qt ファイル | Tk の元 |
|---|---|
| `qt/pages/video.py` | `ui/pages/connection_video.py` の `build_video` |
| `qt/pages/quality.py` | `ui/pages/quality.py` |
| `qt/pages/effects.py` | `ui/pages/effects.py` |
| `qt/pages/audio.py` | `ui/pages/audio.py`（入力レベルメーターは QWidget の paintEvent で描く。色はパレットから） |
| `qt/pages/recording.py` | `ui/pages/recording.py` |
| `qt/pages/status.py` | `ui/pages/status.py`（温度グラフは QWidget の paintEvent。細い線、塗りなし） |
| `qt/pages/shortcuts.py` | `ui/pages/shortcuts.py`（キー割り当ては QKeySequenceEdit 等で。既存の `phonecam_hotkeys` の形式で保存） |
| `qt/pages/settings.py` | `ui/pages/settings.py` + `ui/update_ui.py` の更新確認部分。初期セットアップ / AltServerの診断ボタンは、Tk版ウィザードを別プロセスで開く（`sys.executable` + `--setup` 引数。exe でないときは `phonecam_receiver.py --setup`） |

## ルール
- 画面の文言は必ず `phonecam.i18n.t("…")` を使う。既存キー（`windows/locales/ja.json`）をできるだけ再利用し、足りないキーは `ja.json` と `en.json` の両方に追加（キー順ソート、indent=1、ensure_ascii=False）。
- 色は直書きしない（パレット由来。`widgets` の部品が面倒を見る）。
- Controller に足りない操作があれば `controller.py` に UI 非依存のメソッドとして追加してよい（Tk 側のロジックを移す）。Tk 版の動作は壊さない。
- 各ページは `Page` → `Section` → `Row` で組む。1行 = 「アイコン｜項目名（＋1行の補足）｜右に操作」。
- `windows/qt_smoke.py` が全ページをライト/ダーク・倍率1.0/2.0で撮る。build が例外を出さないこと（demo 状態で表示できること）。

## 確認
- `python -m py_compile` が全ファイル通ること。
- 可能なら `pip install PySide6==6.8.3` して `QT_QPA_PLATFORM=offscreen python windows/qt_smoke.py --out /tmp/shots` が成功すること。
- PR の説明に、ページごとの対応状況（移した項目・移せなかった項目）を日本語で書くこと。
