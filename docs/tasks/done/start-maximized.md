# タスク: 起動時にウィンドウを最大化する

対象: `windows/phonecam/ui/app.py`（Tkinter 版、現在の配布版）

## 要望
起動したとき小さい枠で出て不便。最大化した状態で起動してほしい。

## 仕様
- 起動時は `root.state("zoomed")` で最大化する（Windows）。`root.geometry(...)` による初期サイズは、最大化を解除したときの大きさとして残す。
- 終了時の状態を設定に保存する: 新しい設定キー `window_maximized`（既定 True）を `windows/phonecam/core/settings.py` の DEFAULT_SETTINGS と sanitize に追加。ユーザーが最大化を解除して閉じたら、次回は解除した状態（前回の大きさ・位置）で開く。大きさ・位置はキー `window_geometry`（"WxH+X+Y" 文字列、既定 ""）に保存し、画面外になっていたら無視する。
- 初期セットアップのウィザード（`windows/phonecam_setup.py` の SetupWizard）は今のまま（最大化しない）。
- 既存の動作・他のファイルは変えない。

## 確認
- `python -m py_compile` が通ること。
- 変更点を PR の説明に日本語で短く書くこと。
