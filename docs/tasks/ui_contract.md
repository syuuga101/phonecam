# PhoneCam UI 共通ルール（全担当が守る）

デザインの正解: Design canvas https://claude.ai/artifact/PqNrCRQFipKNt14RsNFR9K
（iPhone: Main/Phone_Dark/Land_*/Settings_*、Windows: Win_*/Win_Settings_*）。迷ったらキャンバスを Artifact read で読む。

## 見た目
- iPhone: iOS標準そのまま。inset grouped List、行の左にSF Symbols（.secondary 色、幅28pxの列にそろえる）、標準Toggle/Picker(.segmented)/NavigationStack。色は system colors のみ（ライト/ダークは自動）。赤 = 配信中/録画、緑の点 = 接続中だけ。大見出し「PhoneCam」は出さず、左上に背景なしのアプリアイコン（28pt、テンプレート描画）。
- Windows: Windows 11 設定アプリ風。テーマは sv-ttk（Sun Valley）、起動時と変化時に darkdetect でOSのライト/ダークに追従（設定で「システムに合わせる/ライト/ダーク」）。フォント Segoe UI Variable → Segoe UI。左ナビ: 接続/映像/画質/エフェクト/音声/録画/状態/ショートカット/設定（各項目の左に16pxの線アイコン）。設定行は「アイコン＋ラベル（＋1行の補足）｜右に操作」のカード。
- プレビュー: iPhoneは横幅いっぱい16:9が初期値、下辺のつまみで高さを変えられる（保存）。Windowsは内容列幅の 2/3 が初期値、右下のつまみで拡縮（保存）。
- 説明文は最小。必要なときだけ1行・二次色。
- 角丸: iOS標準のまま / Windows は sv-ttk 標準のまま。独自の飾り（影・グラデーション・色付きタイル・バッジ）は禁止。

## 多言語化
- Windows: `windows/phonecam/i18n.py` に `t(key, **kw)`。文言は `windows/locales/ja.json`（原文）と `en.json`。キーは `"page.section.item"` 形式（例 `"video.resolution.label"`）。UIに文字列を直書きしない。言語は設定 `language`（"system"/"ja"/"en"…）。
- iPhone: String Catalog `ios/PhoneCam/Localizable.xcstrings`（原文 ja、en を用意）。SwiftUIは `Text("key")` ではなく日本語原文をキーにする（xcstringsの標準運用）。アプリ内の言語切替は設定画面の「言語」→ UserDefaults "AppleLanguages" を設定し「再起動で反映」と案内。
- Crowdin: ルートに `crowdin.yml`（source: windows/locales/ja.json → translation: windows/locales/%two_letters_code%.json、ios/PhoneCam/Localizable.xcstrings は Crowdin の xcstrings 対応で同期）。

## 設定の保存
- Windows: 既存の settings dict と `_changed(key, value, group)` を使う。新キーは DEFAULT_SETTINGS と sanitize に追加。
- iPhone: UserDefaults（@AppStorage）。

## 担当の境界
- 自分の担当ファイル以外は編集しない。共通部品（テーマ・アイコン・i18n）が足りないときは、自分のファイル内に仮実装して報告に書く（共通担当が後で統合）。
- 完了時: py_compile / Swiftは目視確認、テスト、変更ファイル一覧と未解決点を日本語で短く報告。コミットしない。

## Windows 共通部品の約束（外枠担当が実装、ページ担当は先に使ってよい）
`from phonecam.ui.widgets import page, section, row, hint, icon_label` / `from phonecam.i18n import t`
- `page(parent) -> frame`: ページ用のスクロール可能な内容列（左右余白24px）。各ページの build はこれに積む。
- `section(parent, title) -> frame`: 小見出し（Segoe UI Semibold 10pt）＋ その下のカード枠（sv-ttk の Card スタイル、余白12px）。
- `row(card, icon, title, hint=None) -> frame`: 「16pxアイコン｜ラベル（＋1行の二次色補足）」左、戻り値の frame に右側の操作（Combobox/Scale/Checkbutton(Switch.TCheckbutton)/Button）を pack(side="right")。行の間に1pxの区切り線。
- `hint(parent, text)`: 二次色の1行説明（必要なときだけ）。
- アイコン名（外枠担当が `ui/icons.py` で線画PNGをライト/ダーク両方生成）: link, usb, wifi, video, camera, lens, aspect, rotate, flip, gauge, sun, droplet, focus, zoom, light, sparkles, person, palette, mic, speaker, volume, record, folder, thermometer, battery, clock, keyboard, gear, globe, download, wrench, info, heart, document, play, pause. 足りない名前は `icon_label` が空白で代替する。
- 文言は必ず `t("page.section.item")`。担当は自分のキーを `scratchpad/locales/<担当名>.ja.json` と `.en.json` に書く（共有の windows/locales/*.json は統合担当＝Opus がマージ）。
- 色を直書きしない。テーマ切替（ライト/ダーク）で再描画されるよう tk.Canvas 等の色は `phonecam.ui.theme.colors()`（dict: bg, card, text, text2, separator, accent, live）から取り、`theme.on_change(callback)` に登録。
