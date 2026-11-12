# iPhoneアプリの入れ方（AltStore）

PhoneCam は App Store には無いので、**AltStore** という仕組みで iPhone に入れます。Mac は不要です。

受信アプリのセットアップウィザードの最後に **「iPhoneのセットアップ」** が開き、下の手順を 1 つずつ案内します（あとから開くときは「セットアップ」タブの **iPhoneのセットアップ** ボタン）。各ステップには状態表示（✓ / 待機中）があり、2 秒ごとに自動で更新されます。

## 7 ステップ

| # | やること | メモ |
|---|---|---|
| 1 | iPhone を USB でつなぐ | 見つかると自動で ✓ |
| 2 | 「このコンピュータを信頼」 | iPhone のロックを解除して「信頼」を押す。済んでいれば自動で ✓ |
| 3 | iTunes で Wi-Fi 同期をオン | **iTunesを開く** ボタンから。7 日ごとの自動更新に使います（手動で更新するなら省略可） |
| 4 | AltStore を iPhone に入れる | **AltServerを起動** ボタンから。タスクトレイの AltServer アイコン → **Install AltStore** → この iPhone → Apple ID |
| 5 | iPhone の設定 | **設定 > プライバシーとセキュリティ > デベロッパモード** をオンにして再起動。**設定 > 一般 > VPNとデバイス管理** で自分の Apple ID を「信頼」 |
| 6 | PhoneCam を入れる | 画面の **QRコード** を iPhone で読み取り、AltStore にソースを追加して「PhoneCam」を入手 |
| 7 | 接続テスト | iPhone で PhoneCam を開くと、USB 経由でつながったことを自動で確認して ✓ → **完了** |

どの画面でも **戻る / 次へ**、**あとで**（iPhone の準備をあとに回す）が使えます。

## PhoneCam の入れ方（ステップ 6 の詳細）

PhoneCam は **AltStore のソース** として配布しています。ウィザードの QR コードを iPhone のカメラで読み取ると AltStore が開き、ソースを追加できます。追加したら、AltStore で **PhoneCam** を入手します。新しいバージョンが出たときも、AltStore から更新できます。

QR が使えないときは、AltStore の「Sources」に次の URL を貼り付けて追加します。

```
https://github.com/syuuga101/phonecam/releases/latest/download/altstore-source.json
```

### QR が使えないときの別の方法

- **AltServer で直接入れる**: ウィザードの **PhoneCam.ipaをダウンロード** で「ダウンロード」フォルダに保存 → **Shift キーを押しながら** タスクトレイの AltServer アイコンをクリック → **Sideload .ipa…** → `PhoneCam.ipa` を選びます。新しいバージョンは自分で入れ直します。
- **iPhone に保存して AltStore で開く**: iPhone の Safari などで、Releases の `PhoneCam.ipa` を保存し、AltStore の **My Apps > ＋** から選びます。
- **Sideloadly**（https://sideloadly.io/）: `PhoneCam.ipa` をドラッグして Apple ID を入れて Start。7 日ごとに自分で入れ直します。

## 最初の起動

PhoneCam を開いたら、カメラとローカルネットワークの許可を「許可」にします。マイクと写真（録画の保存）は、使う機能を初めて操作したときに許可を求められます。

## 知っておくこと

- **7 日で期限が切れます**（無料の Apple ID の場合）。PC で AltServer が動いていて、同じ Wi-Fi につながっている間は、AltStore が自動で更新します。手動なら AltStore の「My Apps」で **Refresh** します。
- 無料の Apple ID では、AltStore を含めて同時に有効にできるアプリは **3 つまで**です。
- iTunes と iCloud は、**Apple 公式サイトの版**を使います（Microsoft Store 版は不可。AltServer が動きません）。

## うまくいかないとき

- `AltServer.ServerError 2005`: AltStore の「My Apps」で AltStore を一度 **Refresh** してから、もう一度入れます。
- `requires additional permissions not specified by the source`: PhoneCam を最新版にして（v1.3.5 以降）、ソースを入れ直してください。
- ほかは [よくある質問とトラブル](%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E8%B3%AA%E5%95%8F%E3%81%A8%E3%83%88%E3%83%A9%E3%83%96%E3%83%AB.md) へ。
