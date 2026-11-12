# PhoneCam への協力のしかた

PhoneCam に興味を持ってくれてありがとうございます。不具合の報告、要望、ドキュメントの直し、コードの変更など、どれも歓迎です。

## 不具合を報告する

1. まず [ガイドの「よくある質問とトラブル」](https://github.com/syuuga101/phonecam/blob/main/docs/guide/%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E8%B3%AA%E5%95%8F%E3%81%A8%E3%83%88%E3%83%A9%E3%83%96%E3%83%AB.md) と、[既存の Issue](https://github.com/syuuga101/phonecam/issues) を見てください。
2. 解決しなければ、[新しい Issue](https://github.com/syuuga101/phonecam/issues/new/choose) で「不具合の報告」を選びます。
3. テンプレートの項目（PhoneCam のバージョン、Windows / iPhone の環境、接続のしかた、出力先、再現の手順）を、わかる範囲で書いてください。受信アプリの **「状態」タブ** の内容を貼り付けてもらえると、原因を見つけやすくなります。

**セキュリティの問題**（他人にカメラや音声をのぞかれる可能性など）は、公開の Issue に書かず、[SECURITY.md](SECURITY.md) の方法で報告してください。

## 開発の準備（概要）

- iPhone アプリ（`ios/`）と Windows 受信アプリ（`windows/`）は、どちらも GitHub Actions でビルドします（Mac は不要）。
- Windows 側はソースから実行もできます（Python 3.12、`windows/install.bat` → `start.bat`）。
- くわしくは [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)、しくみは [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、通信の仕様は [docs/PROTOCOL.md](docs/PROTOCOL.md) を見てください。

## プルリクエスト

1. リポジトリを fork して、`main` から作業用のブランチを作ります。
2. 変更はなるべく小さく、1 つの目的にまとめてください。
3. Pull Request を作ると、テンプレートが開きます。概要・変更点・動作確認を書いてください。実機での確認ができているとうれしいです。
4. 通信プロトコルを変えるときは、古いアプリとも動くようにしてください（追加のみ。未知の種別・キーは無視する）。

### コミットメッセージ

- 日本語でも英語でも構いません。1 行目に「何をしたか」を短く書き、必要なら空行のあとに箇条書きで補足します。
- リリースのコミットは `v1.4.0: 変更の要約` のように、バージョンを先頭に付けます。

### バージョンと変更履歴

- ユーザーに見える変更（新機能・修正・仕様の変更）は `CHANGELOG.md` の先頭の項目に、ユーザー向けの言葉で書いてください。
- リリースは `VERSION` ファイルを書き換えて `main` に push すると始まります（`CHANGELOG.md` の `## v<バージョン>` の項目がリリースノートになります）。`VERSION` を変えるのはメンテナーです。ふつうの Pull Request では `VERSION` は変えなくて構いません。

### 秘密情報を入れない

パスワード、トークン、Apple ID、証明書、自分の IP アドレスやペアリングコードなどを、コード・Issue・スクリーンショットに含めないでください。入ってしまったときはすぐに知らせてください。

## ライセンス

PhoneCam のライセンスは [GPL-2.0以降（GPL-2.0-or-later）](LICENSE) です。Pull Request で送ってもらった内容は、同じライセンスのもとで公開されることに同意したものとして受け付けます。

## ドキュメントの直し

誤字の修正や説明の改善も大歓迎です。`README.md` と `docs/`（使い方ガイドは `docs/guide/`）は、Pull Request で直接直せます。
