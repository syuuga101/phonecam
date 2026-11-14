# タスク: テストをリポジトリに整備して CI で毎回回す（Codex向け）

## 背景
- 今のリポジトリには、まとまったテストがない。
- 過去に書いたテストは外部の作業用フォルダにあり、Tk 時代のものが多く古くなっている。
- 修正で壊れても気づけないので、Windows 受信アプリのロジック部分のテストを `tests/` にそろえ、GitHub Actions で毎回実行したい。

## やること
1. `windows/tests/` を作り、pytest で書く。Qt や Windows 固有の API に依存しない部分を中心にする。
   - `core/settings.py`: 既定値、範囲の検証（ビットレート・プレビュー fps・自動停止時間など）、古い設定ファイルからの移行（複数カメラ化を含む）
   - `core/canvas.py`: build_control、clamp、flip_image（旧 numpy 実装と同じ結果になること）
   - `core/link.py`: 接続先の決め方（_resolve: USB 優先・登録済み端末・Wi-Fi のみ）。ソケットや usbmux はモックする。送信スレッドの合体送信、切断・再接続
   - `core/decoder.py`: BacklogGuard、FrameMailbox
   - `core/stream.py`: 使用中の検出、自動停止と再開（idle_stop_sec）、手動停止との区別
   - `core/wireless.py`: QR ペアリングの受付（トークンの一回限り・期限切れ・不正入力・同時接続）、LAN の IPv4 の判定、known_devices の検証
   - `core/cameras.py`: assign / unassign、スロットの割り当て、USB の端末 ID の扱い（1台と複数台）
   - `core/softcam_reg.py`: rev 方式の登録判定（missing / outdated / ok）、パス比較。レジストリとファイルはモックする
   - `phonecam_setup.py`: GuidedFlow、SetupEngine、SetupController の状態の移り変わり。検出とダウンロードはモックする
   - `phonecam/i18n.py` と locales: ja と en のキーの一致、t() の kwargs とプレースホルダの一致（コードを AST で走査）、全キーが例外なく整形できること
   - `controller.py`: phoneEdit の反映（zoom、torch、lens、micMuted）、status の間引き、更新確認のタイムアウト
2. Qt に依存する部分は、`QT_QPA_PLATFORM=offscreen` で動く軽いテストを数本だけ入れる。対象はカメラページのタイル数、ウィザードの段階移動など。重い画面テストは既存の qt_smoke.py に任せる。
3. CI に入れる
   - `.github/workflows/windows.yml` に、ビルドの前に pytest を走らせるジョブ（Windows ランナー）を足す。
   - Linux ランナーで走らせられる部分は、速い Linux のジョブでもよい。
   - 失敗したらビルドとリリースを止める。
4. `docs/DEVELOPMENT.md` に、テストの実行方法を書く。

## 守ること
- 始める前に `git merge origin/main` で最新を取り込む（v2.9.0 以降。Tk は削除済み）。
- アプリの挙動は変えない。テストのために必要な小さなリファクタ（依存の注入など）は可。その場合は理由を PR に書く。
- テストで見つけたバグは、直さずに PR の説明に列挙する。小さく確実なものだけは直してよい。直した場合は、どう直したかも書く。
- 確認できたこと・できなかったことを、PR の説明に日本語で書く。
