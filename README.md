<p align="center">
  <img src="docs/icon.png" width="96" alt="PhoneCam">
</p>

# PhoneCam

**使っていない iPhone を、Windows 用の低遅延ウェブカメラ＆マイクにするアプリです。**
USB ケーブルでつなぐだけで、OBS・Discord・Zoom などに「PhoneCam」というカメラが現れます。Mac は不要で、無料で使えます。

## できること

- iPhone の高画質カメラを、USB（または Wi-Fi）でつないで PC のカメラとして使えます
- 遅延が小さい（USB・1080p60 でおおよそ 50〜100ms が目標）
- OBS の「仮想カメラ開始」と同時に使える専用カメラ
- iPhone のマイクを、OBS や Discord のマイクとして使える（VB-CABLE を使います）
- 背景ぼかし・背景の切り抜き（緑）、明るさや色の調整、露出・ピント・ズームの操作
- 広角 / 超広角 / インカメの切り替え、4K 出力、iPhone 内での高画質録画
- 使っていないときはカメラを止めて、発熱とバッテリーを節約
- 初回は **セットアップウィザード** が必要なソフトを自動で用意します

## 必要なもの

| | |
|---|---|
| iPhone | iOS 17 以上（iPhone 13 で動作確認） |
| PC | Windows 10 / 11 |
| ケーブル | USB ケーブル（iPhone に付属のもの） |
| そのほか | Apple ID（無料のものでOK）。iPhone にアプリを入れるのに使います |

## はじめかた（3ステップ）

1. **ダウンロード**: [Releases](https://github.com/syuuga101/phonecam/releases/latest) から `PhoneCamReceiver.exe` をダウンロードします。
2. **セットアップ**: `PhoneCamReceiver.exe` をダブルクリックして、**セットアップウィザード** の案内に沿って進めます（iPhone へのアプリのインストールも案内します）。
3. **カメラを選ぶ**: OBS の「映像キャプチャデバイス」や Discord のカメラで **「PhoneCam」** を選びます。

> Windows の青い警告（SmartScreen）が出たら、**詳細情報 → 実行** を選んでください。PhoneCam の exe には、まだ署名がついていないためです。
> 詳しい手順は [Wiki のはじめに](https://github.com/syuuga101/phonecam/wiki/はじめに（セットアップ）) を見てください。

## よくある質問

**Q. 「PhoneCam」がカメラ一覧に出ません**
受信アプリを先に起動してください。それでも出ないときは、「接続・映像」タブの「PhoneCamカメラ」にある **登録する（更新）** を押します。

**Q. 映像が出ません（真っ黒）**
iPhone で PhoneCam アプリを開いたまま、USB でつないでいますか？ 受信アプリで「接続」して、映像が流れているか確認してください。Discord は一度カメラをオフ→オンにします。

**Q. iPhone にアプリを入れるときに `AltServer.ServerError 2005` が出ます**
AltStore の「My Apps」で AltStore を一度 **Refresh** してから、もう一度入れてください。

**Q. 数日たつとアプリが開かなくなりました**
無料の Apple ID では、アプリは 7 日で期限が切れます。PC で AltServer を動かして、iPhone と同じ Wi-Fi につなぎ、AltStore の「My Apps」で更新してください。

**Q. Wi-Fi でつなぎたい**
安全のため、Wi-Fi は初期状態でオフです。iPhone アプリで「Wi-Fiからの接続を許可」をオンにし、画面に出る 6 桁のコードを PC に入力します。安定して遅延も小さいのは USB です。

**Q. VB-CABLE を入れたら PC の音が出なくなりました**
タスクバーのスピーカーのアイコンから、出力先を元のスピーカー／ヘッドホンに戻してください（PhoneCam も自動で戻します）。

そのほかの質問は [よくある質問とトラブル](https://github.com/syuuga101/phonecam/wiki/よくある質問とトラブル) にまとめています。

## 使い方のガイド（Wiki）

- [はじめに（セットアップ）](https://github.com/syuuga101/phonecam/wiki/はじめに（セットアップ）)
- [iPhoneアプリの入れ方（AltStore）](https://github.com/syuuga101/phonecam/wiki/iPhoneアプリの入れ方（AltStore）)
- [OBSで使う](https://github.com/syuuga101/phonecam/wiki/OBSで使う) / [Discordで使う](https://github.com/syuuga101/phonecam/wiki/Discordで使う)
- [音声（iPhoneのマイク）を使う](https://github.com/syuuga101/phonecam/wiki/音声（iPhoneのマイク）を使う)
- [画質と遅延の調整](https://github.com/syuuga101/phonecam/wiki/画質と遅延の調整) / [録画機能](https://github.com/syuuga101/phonecam/wiki/録画機能)
- [アップデート方法](https://github.com/syuuga101/phonecam/wiki/アップデート方法)

## 困ったときは・ご意見

- 不具合や要望は [Issues](https://github.com/syuuga101/phonecam/issues) へ（テンプレートがあります）
- 脆弱性（セキュリティの問題）は [SECURITY.md](SECURITY.md) の方法で非公開で報告してください
- 変更履歴は [CHANGELOG.md](CHANGELOG.md)

## 開発者向け

ソースからのビルド、しくみ、通信プロトコルなどは `docs/` にあります。

- [DEVELOPMENT.md](docs/DEVELOPMENT.md): ビルド・リポジトリの構成・リリースの手順
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): iPhone / PC のパイプライン、仮想カメラ、音声、遅延の設計
- [PROTOCOL.md](docs/PROTOCOL.md): 通信プロトコル
- [CONTRIBUTING.md](CONTRIBUTING.md): 協力のしかた

## ライセンス

ライセンス: [GPL-2.0以降](LICENSE)（GPL-2.0-or-later）。使っている部品のライセンスは [THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md) にあります。

## 謝辞

専用カメラは [softcam](https://github.com/tshino/softcam)（MIT License）を元にしています。
