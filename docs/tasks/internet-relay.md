# タスク: 離れた場所からインターネット経由でつなぐ（WebRTC + TURN）（Codex向け）

## ゴール
PC と iPhone が同じネットワークにいなくても、インターネット経由で自動的につながるようにする。
- 例1: 大学の Wi-Fi のように、端末どうしの通信が遮断されている。
- 例2: iPhone は楽天モバイル回線、PC は家にある。

ユーザーは方式を選ばない。今の自動切り替え（USB → 登録済み端末を LAN で探す）で見つからないとき、最後の手段として自動でインターネット経由を試す。

## 方針
- **WebRTC の DataChannel を使い、今の PhoneCam の通信をそのまま流す。** 映像は今の H.264/HEVC のまま。WebRTC のメディア経路で再エンコードはしない。
  - 映像は順序なし・再送なし（unordered, maxRetransmits=0）のチャンネルで送る。キーフレーム要求は既存の仕組みを使う。
  - 制御・状態・ping は、順序あり・信頼性ありのチャンネルで送る。
  - 音声は、遅延が許せば映像と同じ扱い。難しければ今回は無しでよい。理由を PR に書く。
- **接続経路**: 直接つながる場合は直結（P2P、STUN で外向きアドレスを調べる）を使い、つながらない場合だけ中継（TURN）を使う。
  - TURN は Cloudflare Realtime TURN を使う。無料枠は 1,000GB。TURN over TLS 443 を含め、UDP が塞がれた大学のネットワークでも通るようにする。
- **シグナリング（お互いを見つけて接続情報を交換する仕組み）**: Cloudflare Workers + Durable Objects で、小さなサーバーを作る。
  - Worker のコードは `server/relay-worker/` に置く。`wrangler.toml` と README（デプロイ手順）も付ける。
  - Worker は TURN の短期認証情報を発行する。Cloudflare の TURN キーは Worker の secret に置き、アプリには入れない。
  - 部屋 ID は、QR ペアリングのときに PC と iPhone で共有するランダムな秘密から作る。他人が推測して入れないようにする。
  - Worker は SDP/ICE の中継だけを行い、映像は通さない。
- **認証**: ペアリングコードによる照合は今までどおり iPhone 側で行い、DataChannel の上で同じ auth を通す。DTLS で暗号化はされるが、認証を弱めない。

## 実装場所
- **Windows**: `aiortc`（Python の WebRTC）を使う。requirements と PyInstaller に追加する。
  - core/link.py の接続先の決め方（`_resolve`）に「インターネット経由」を最後の候補として足す。
  - DataChannel を今のソケットと同じ形で扱えるアダプタを作り、受信・デコード以降は変えない。
- **iOS**: WebRTC フレームワークを使う（例: Swift Package の `stasel/WebRTC`。project.yml に追加）。
  - StreamServer と同じメッセージを DataChannel に流すアダプタを作る。
  - アプリがバックグラウンドのときの扱いは、今の Wi-Fi と同じでよい。
- **UI**:
  - 接続ページの上部カードに「インターネット経由」と、直結か中継かを出す。
  - 状態ページに経路と往復の遅延（RTT）を出す。
  - 設定に「インターネット経由でつなぐ」スイッチを置く。既定はオン。
  - Worker の URL は設定で変えられるようにする。既定値は PR で決める（例: ユーザーが自分でデプロイした URL を入れる欄）。
- 文言は ja/en 両方に入れる。iOS は Localizable.xcstrings に入れる。docs/PROTOCOL.md と docs/ARCHITECTURE.md を更新する。

## 守ること
- USB と LAN の接続を、今より遅くしない。インターネット経由は、それらで見つからないときだけ使う。
- 遅延を増やすバッファを入れない。映像は最新フレームを優先し、古いフレームは捨てる（今の方針と同じ）。
- 始める前に `git merge origin/main` で最新を取り込む（v2.5.0、複数カメラ対応済み。インターネット経由も端末ごとに動くようにする）。
- PR #7（自動ビットレート）と同時に進む場合は、Wi-Fi のときの自動調整がインターネット経由でも効くようにする。

## 確認
- Worker は `wrangler dev`（ローカル）で動作を確認する。デプロイ手順は README に書く。デプロイにはユーザーの Cloudflare アカウントが必要。
- Windows で exe をビルドし、iPhone 実機を楽天モバイル回線にした状態でつなぐ（PC は自宅の回線）。直結・中継のどちらになったかと、遅延を記録する。
- iOS は CI のビルド結果で確認する。
- 確認できたこと・できなかったことを、PR の説明に日本語で書く。
