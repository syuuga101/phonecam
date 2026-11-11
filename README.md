# PhoneCam

iPhone 13 を、Windows の OBS で使える低遅延ワイヤレス／USB カメラにするプロジェクトです。
Mac は不要です。iPhone アプリと Windows 受信アプリ(exe)は GitHub Actions で自動ビルドし、AltStore で iPhone に入れます。

## 概要と仕組み

```
iPhone (PhoneCam アプリ)                         Windows PC
 カメラ → H.264 ハードウェアエンコード  ── USB (usbmux) / Wi-Fi ──→  受信アプリ → デコード → 仮想カメラ → OBS / Discord
                                    ←──────  露出・WB・フォーカス等の制御 ──────
```

- iPhone 側は TCP サーバ（ポート 5555）として待ち受けます。
- USB 接続では、Apple Devices アプリ（または iTunes）が入れる「Apple Mobile Device Service」を経由して接続します（専用ドライバー不要）。
- Wi-Fi 接続では、iPhone の画面に表示される IP アドレスへ直接接続します。
- 映像は H.264 のまま送られ、PC 側でデコードして仮想カメラに流します。
- 露出・ホワイトバランス・フォーカス・ズーム・ライトは PC の画面から操作できます。

## 必要なもの

- GitHub アカウント（無料）
- Windows PC
- Python 3.12（exe 版を使うなら不要）
- OBS Studio 30 以上
- iTunes と iCloud（Apple 公式サイト版。AltStore 用。USB 接続の通信にも使います）
- AltServer（https://altstore.io/）
- Lightning ケーブル（USB 接続で使う場合。Wi-Fi のみなら不要ですが、充電のためにもおすすめです）
- Apple ID（無料のものでOK）

## iPhoneアプリのビルド

1. GitHub で新しいリポジトリを作成します（Private でも構いません）。
2. このフォルダ（`phonecam`）の中身を、そのリポジトリへ push します。
   ```
   git init
   git add .
   git commit -m "PhoneCam"
   git branch -M main
   git remote add origin https://github.com/あなたのID/リポジトリ名.git
   git push -u origin main
   ```
3. GitHub のリポジトリページで **Actions** タブを開きます。
4. 左の「Build iOS (unsigned IPA)」を選び、**Run workflow** を押します（push しただけでも自動で動きます）。
5. 数分で完了します。実行結果のページ下部「Artifacts」から **PhoneCam-ipa** をダウンロードします。
6. ダウンロードした zip を展開すると `PhoneCam.ipa` が入っています。

## iPhoneへのインストール（AltStore Classic 推奨）

1. Apple の公式サイトから **iTunes** と **iCloud** をインストールします。**Microsoft Store 版は不可**です（AltServer が動きません）。
2. **AltServer** をインストールして起動します（タスクトレイに常駐します）。
3. iPhone を USB で接続して「このコンピュータを信頼」し、iTunes で iPhone を開いて **「Wi-Fi経由でこのiPhoneと同期」** を ON にします。
4. タスクトレイの AltServer アイコン > **Install AltStore** > 自分の iPhone を選び、Apple ID を入力します。
5. iPhone の **設定 > プライバシーとセキュリティ > デベロッパモード** を ON にして再起動します。
6. iPhone の **設定 > 一般 > VPNとデバイス管理** で自分の Apple ID のアプリを開き、「信頼」します。
7. iPhone で **AltStore** を開き、**My Apps** > 左上の **＋** > `PhoneCam.ipa`（iPhone に保存しておく）を選びます。
8. PhoneCam を起動し、カメラとローカルネットワークの許可を「許可」にします。

- 7日の期限は、PC で AltServer が動いていて同じ Wi-Fi につながっている間、AltStore が自動で更新します。
- 無料の Apple ID では、AltStore を含めて同時に有効にできるサイドロードアプリは **3つまで**です。

> 代わりに Sideloadly（https://sideloadly.io/）でも入れられます。PC で `PhoneCam.ipa` をドラッグして Apple ID を入力し Start するだけですが、7日ごとに自分で再インストールが必要です。

## Windows側（exe 版・おすすめ）

1. GitHub の **Actions** タブ > **Build Windows receiver** > 最新の実行 > 下部の **Artifacts** から **PhoneCamReceiver-exe** をダウンロードし、zip を展開します。
2. `PhoneCamReceiver.exe` をダブルクリックします。Windows SmartScreen の警告が出たら **詳細情報 → 実行** を選びます。
3. 設定は exe と同じフォルダの `phonecam_settings.json` に保存されます。

Python は不要です。OBS Studio 30 以上と、USB 接続なら iTunes が必要です。

## Windows側（ソースから実行）

1. `windows` フォルダの **install.bat** をダブルクリックします（初回のみ。av / numpy / pyvirtualcam をインストールします）。
2. iPhone で PhoneCam を起動し、画面を表示したままにします。
3. **start.bat** をダブルクリックすると受信アプリが開きます。
4. 「USB」を選んで **接続** を押します。Wi-Fi の場合は「Wi-Fi」を選び、iPhone の画面に表示された IP アドレス（`:5555` の前の部分）を入力して接続します。
5. 切断されても、接続中の間は 2 秒ごとに自動で再接続します。

設定は `windows/phonecam_settings.json` に自動保存され、次回起動時に復元されます。

## OBSでの使い方

1. 受信アプリで接続し、映像が流れていることを確認します（受信fps が表示されます）。
2. OBS で **ソース追加 > 映像キャプチャデバイス** を作成します。
3. デバイスに **OBS Virtual Camera** を選びます。

注意:
- OBS の「**仮想カメラ開始**」ボタンとは同時に使えません。OBS 側の仮想カメラは開始しないでください。
- Discord などでも、カメラに **OBS Virtual Camera** を選べば PhoneCam の映像を使えます。
- 出力先は受信アプリの「映像 > 出力先」で、OBS Virtual Camera か Unity Video Capture を選べます。

## おすすめ設定

- 接続: **USB**（Wi-Fi より安定し、遅延も小さい）
- 映像: **1080p / 60fps / ビットレート 20Mbps**
- 向き（映像 > 向き）: **自動**（iPhone を縦に持てば縦映像、横に持てば横映像）／**横**（常に 1920×1080）／**縦**（常に 1080×1920）。向きが変わると映像サイズも変わるため、仮想カメラは自動で作り直されます（OBS 側で一瞬途切れることがあります）。OBS のキャンバスを縦にするなら「縦」固定がおすすめです。
- 露出・ホワイトバランス・フォーカス: **マニュアル／固定**（配信中に明るさや色が勝手に変わらなくなります）
- シャッター速度（照明のちらつき防止）:
  - 東日本（50Hz）: **1/50** か **1/100**
  - 西日本（60Hz）: **1/60** か **1/120**
- 長時間使うときは、iPhone の **設定 > バッテリー > 充電最適化** で充電上限を 80% にし、風通しの良い場所に置いてください。
- iPhone の「画面を暗くする」を ON にすると、発熱とバッテリー消費を抑えられます。

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| 「Apple Mobile Device Service に接続できません」 | iTunes（または Microsoft Store の「Apple Devices」アプリ）をインストールし、PCを再起動してください。 |
| 「iPhoneがUSBで見つかりません」 | ケーブルを確認し、iPhone のロックを解除して「このコンピュータを信頼」してください。 |
| 「iPhoneのPhoneCamアプリに接続できません」 | iPhone で PhoneCam を起動し、前面に表示したままにしてください。 |
| Wi-Fi で接続できない | iPhone と PC が同じネットワークか確認してください。初回はローカルネットワークの許可が必要です。 |
| 仮想カメラを開始できない | OBS Studio 30以上をインストールしてください。OBS の「仮想カメラ開始」が ON なら停止してください。 |
| 映像が固まる／乱れる | 受信アプリが自動でキーフレームを再要求します。改善しない場合はビットレートを下げるか USB を使ってください。 |
| 映像が暗い／ちらつく | 露出をマニュアルにし、シャッター速度を地域に合わせてください（上記参照）。 |
| 数日後にアプリが開かなくなった | 無料 Apple ID は7日で期限切れです。AltStore なら PC の AltServer を起動して同じ Wi-Fi につなぎ、My Apps で更新してください（Sideloadly の場合は再インストール）。 |
| 画面が消えて止まる | iPhone をロックするとカメラが止まります。アプリを前面に表示したままにしてください。 |
| `py` コマンドが見つからない | Python 3.12 を公式サイトからインストールし、「Add python.exe to PATH」と「py launcher」にチェックを入れてください。 |

## 遅延について

目標は、USB 接続・1080p60 で **おおよそ 50〜100ms** です（理論上の目標値で、環境により変わります）。
遅延を小さくするため、次の工夫をしています。

- iPhone: 手ブレ補正オフ、遅れたフレームは破棄、低遅延レート制御、Bフレームなし
- 通信: TCP_NODELAY、送信が詰まったときはキーフレーム以外を破棄
- PC: キューを使わず受信スレッドでそのままデコードして出力

さらに小さくしたいときは、USB 接続にし、ビットレートを上げすぎず、PC の負荷を下げてください。
OBS 側でもソースのバッファリングを無効にすると改善する場合があります。
