# タスク: Wiki の SideStore 関連記事を初心者向けに仕上げる

## 目的（ユーザーの要望）
「wiki の sideload らへんの記事を拡充して、初心者でも wiki をフォローすればしっかりダウンロードできるようにしよう。画像の協力とかするから、わかりやすさを目標にまとめて」

## 現状
- `wiki-draft/` は Wiki（syuuga101/phonecam.wiki）の全ページのコピー。
- 「iPhoneアプリの入れ方（SideStore）.md」と「アップデート方法.md」は、Claude が書き直し途中（Wiki 本体にはまだ反映していない）。
- 「よくある質問とトラブル.md」「Home.md」「_Sidebar.md」は未着手。

## やること
1. 書き直し途中の 2 ページを読み、下の「書き方」に沿って完成させる。
2. 「よくある質問とトラブル」の SideStore 関係、Home、_Sidebar を揃える。
3. 撮影リスト `wiki-draft/SHOTLIST.md` を作る。
   - 表の列: ファイル名、どの記事のどこ、PC/iPhone、何の画面を写すか、隠すべき個人情報（Apple ID・電話番号・UDID など）。
   - 枚数は 15〜25 枚程度。
4. 本体の手順と食い違わないようにする。照合先は次のとおり。
   - `windows/phonecam_setup.py`
   - `windows/phonecam/qt/setup_wizard.py`（8 ステップ: usb, trust, iloader, devmode, vpn, sidestore, phonecam, test）
   - README
5. SideStore / iloader / LocalDevVPN / LiveContainer の公式ドキュメントで手順を確認する。分からない所は推測で書かず、PR に「要確認」として挙げる。

## 書き方
- 対象は初めてサイドロードする人。
  - 専門用語は最初に一言で説明する。
  - 1 ステップ 1 操作にする。
  - 各ステップに「こうなれば OK」を書く。
- 冒頭に「全体の流れ」（段階と時間の目安）と「用意するもの」チェックリストを置く。
- よくあるつまずきは、段階ごとに `<details>` で付ける。必ず入れるものは次のとおり。
  - VPN エラー → LocalDevVPN の Tunnel IP を `10.7.0.0/32` にする（実際に解決済み）。
  - 「Verification codes can't be sent to this phone number」→ Apple の SMS 回数制限。時間を置くか、信頼済みデバイスで受け取る。
  - LiveContainer の error 1006 UDID → ペアリングファイルを作り直す。
  - 無料 Apple ID の制限（アプリ 3 個まで、7 日ごとに更新）。面倒な人は SideStore 単体で入れて更新すればよい。LiveContainer は上級者向けの任意手順。
  - デベロッパモードを有効にする。
- 7 日ごとの更新のやり方と、期限切れのときの直し方を書く。
- ソース URL: `https://github.com/syuuga101/phonecam/releases/latest/download/sidestore-source.json`
- です・ます調。煽りは入れない。
- 画像はユーザーが後で撮る。置く場所には次の 1 行だけを書く。
  `> 📷 画像: images/sidestore/NN-名前.png（何を写すか）`
  壊れた画像リンクは置かない。

## 反映
- この PR がマージされたら、`wiki-draft/` の中身（SHOTLIST.md を除く）を Wiki リポジトリにコピーして push する。
  - Claude 側の環境からは Wiki に push できないので、Codex/ユーザー側で行う。
