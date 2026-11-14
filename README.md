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
- iPhone を横向きにしてもプレビューが横向きで表示され、PC に映る範囲の枠・向き表示・グリッドで構図を決められます（v1.5.0）
- カメラの切り替えとマイクのミュートを iPhone の画面からもでき、PC に反映されます（v1.5.0）
- 水平メーターで、iPhone を真っすぐ置けているか確認できます（v1.5.0）
- PC とつながったときに、iPhone が振動とお知らせでわかります（v1.5.0）
- OBS やゲーム中でも使える **ショートカットキー**（カメラ切り替え・ミュート・背景ぼかし・録画など）（v1.5.0）
- 使っていないときはカメラを止めて、発熱とバッテリーを節約
- 初回は **セットアップウィザード** が必要なソフトを自動で用意します

## ご利用にあたって

PhoneCam は、**ご自分のカメラとマイクを、配信やビデオ通話で使うためのアプリ** です。

- **他の人を、同意なしに撮影・録音することは禁止です。** 盗撮など、法律に反する目的では使わないでください。
- 人が映る・声が入る配信や録画をするときは、その人の同意をとってください。
- 撮影中であることは、わかるようにしてあります。iPhone ではカメラ使用中の緑のドットが出て、PhoneCam の画面にも「配信中」と表示されます。これらを隠すための機能は、今後も付けません。
- 使い方によって起きたトラブルや損害について、作者は責任を負えません。ルールとマナーを守って、気持ちよく使ってください。

セキュリティの問題を見つけたときは [SECURITY.md](SECURITY.md) をご覧ください。

## 必要なもの

| | |
|---|---|
| iPhone | iOS 17 以上（iPhone 13 で動作確認） |
| PC | Windows 10 / 11 |
| ケーブル | USB ケーブル（iPhone に付属のもの） |
| そのほか | Apple ID（無料のものでOK）。iPhone にアプリを入れるのに使います。iPhone には **SideStore** で入れます（最初の 1 回だけ PC と USB でつなぎます） |

## はじめかた（3ステップ）

1. **ダウンロード**: [Releases](https://github.com/syuuga101/phonecam/releases/latest) から `PhoneCamReceiver.exe` をダウンロードします。
2. **セットアップ**: `PhoneCamReceiver.exe` をダブルクリックして、**セットアップウィザード** の案内に沿って進めます（iPhone へのアプリのインストールも案内します）。
3. **カメラを選ぶ**: OBS の「映像キャプチャデバイス」や Discord のカメラで **「PhoneCam」** を選びます。

> Windows の青い警告（SmartScreen）が出たら、**詳細情報 → 実行** を選んでください。PhoneCam の exe には、まだ署名がついていないためです。
> 詳しい手順は [ガイドのはじめに](https://github.com/syuuga101/phonecam/wiki/%E3%81%AF%E3%81%98%E3%82%81%E3%81%AB%EF%BC%88%E3%82%BB%E3%83%83%E3%83%88%E3%82%A2%E3%83%83%E3%83%97%EF%BC%89) を見てください。

## よくある質問

**Q. 「PhoneCam」がカメラ一覧に出ません**
受信アプリを先に起動してください。それでも出ないときは、「接続・映像」タブの「PhoneCamカメラ」にある **登録する（更新）** を押します。

**Q. 映像が出ません（真っ黒）**
iPhone で PhoneCam アプリを開いたまま、USB でつないでいますか？ 受信アプリで「接続」して、映像が流れているか確認してください。Discord は一度カメラをオフ→オンにします。

**Q. iPhone にアプリを入れたい・更新できません**
SideStore で入れます。更新するときは、iPhone で **LocalDevVPN** を Connect にして、**Wi-Fi** につないでください（モバイル回線ではできません）。くわしくは [iPhoneアプリの入れ方（SideStore）](https://github.com/syuuga101/phonecam/wiki/iPhone%E3%82%A2%E3%83%97%E3%83%AA%E3%81%AE%E5%85%A5%E3%82%8C%E6%96%B9%EF%BC%88SideStore%EF%BC%89) と [よくある質問とトラブル](https://github.com/syuuga101/phonecam/wiki/%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E8%B3%AA%E5%95%8F%E3%81%A8%E3%83%88%E3%83%A9%E3%83%96%E3%83%AB) へ。

**Q. 数日たつとアプリが開かなくなりました**
無料の Apple ID では、アプリは 7 日で期限が切れます。iPhone で LocalDevVPN を Connect にして Wi-Fi につなぎ、SideStore の「My Apps」で、アプリの右の「7 DAYS」を押して更新してください。

**Q. Wi-Fi でつなぎたい**
安全のため、Wi-Fi は初期状態でオフです。iPhone アプリで「Wi-Fiからの接続を許可」をオンにし、画面に出る 6 桁のコードを PC に入力します。安定して遅延も小さいのは USB です。

**Q. VB-CABLE を入れたら PC の音が出なくなりました**
タスクバーのスピーカーのアイコンから、出力先を元のスピーカー／ヘッドホンに戻してください（PhoneCam も自動で戻します）。

そのほかの質問は [よくある質問とトラブル](https://github.com/syuuga101/phonecam/wiki/%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E8%B3%AA%E5%95%8F%E3%81%A8%E3%83%88%E3%83%A9%E3%83%96%E3%83%AB) にまとめています。

## 使い方ガイド

- [はじめに（セットアップ）](https://github.com/syuuga101/phonecam/wiki/%E3%81%AF%E3%81%98%E3%82%81%E3%81%AB%EF%BC%88%E3%82%BB%E3%83%83%E3%83%88%E3%82%A2%E3%83%83%E3%83%97%EF%BC%89)
- [iPhoneアプリの入れ方（SideStore）](https://github.com/syuuga101/phonecam/wiki/iPhone%E3%82%A2%E3%83%97%E3%83%AA%E3%81%AE%E5%85%A5%E3%82%8C%E6%96%B9%EF%BC%88SideStore%EF%BC%89)
- [OBSで使う](https://github.com/syuuga101/phonecam/wiki/OBS%E3%81%A7%E4%BD%BF%E3%81%86) / [Discordで使う](https://github.com/syuuga101/phonecam/wiki/Discord%E3%81%A7%E4%BD%BF%E3%81%86)
- [音声（iPhoneのマイク）を使う](https://github.com/syuuga101/phonecam/wiki/%E9%9F%B3%E5%A3%B0%EF%BC%88iPhone%E3%81%AE%E3%83%9E%E3%82%A4%E3%82%AF%EF%BC%89%E3%82%92%E4%BD%BF%E3%81%86)
- [ショートカットキー](https://github.com/syuuga101/phonecam/wiki/%E3%82%B7%E3%83%A7%E3%83%BC%E3%83%88%E3%82%AB%E3%83%83%E3%83%88%E3%82%AD%E3%83%BC)
- [画質と遅延の調整](https://github.com/syuuga101/phonecam/wiki/%E7%94%BB%E8%B3%AA%E3%81%A8%E9%81%85%E5%BB%B6%E3%81%AE%E8%AA%BF%E6%95%B4) / [録画機能](https://github.com/syuuga101/phonecam/wiki/%E9%8C%B2%E7%94%BB%E6%A9%9F%E8%83%BD)
- [アップデート方法](https://github.com/syuuga101/phonecam/wiki/%E3%82%A2%E3%83%83%E3%83%97%E3%83%87%E3%83%BC%E3%83%88%E6%96%B9%E6%B3%95)

## 困ったときは・ご意見

- 不具合や要望は [Issues](https://github.com/syuuga101/phonecam/issues) へ（テンプレートがあります）
- 脆弱性（セキュリティの問題）は [SECURITY.md](SECURITY.md) の方法で非公開で報告してください
- 変更履歴は [CHANGELOG.md](CHANGELOG.md)

## 翻訳に協力する

画面の文言を他の言語にする作業を [Crowdin](https://crowdin.com/project/phonecam) で募集しています。Crowdin のアカウントがあれば、ブラウザー上で翻訳できます（元の言語は日本語です）。

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
