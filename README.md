# PhoneCam

iPhone 13 を、Windows の OBS で使える低遅延ワイヤレス／USB カメラにするプロジェクトです。
Mac は不要です。iPhone アプリと Windows 受信アプリ(exe)は GitHub Actions で自動ビルドし、AltStore で iPhone に入れます。

## 概要と仕組み

```
iPhone (PhoneCam アプリ)                         Windows PC
 カメラ → 背景・色調整 → H.264/HEVC エンコード ── USB (usbmux) / Wi-Fi ──→  受信アプリ → デコード → 仮想カメラ → OBS / Discord
                                    ←──────  露出・WB・フォーカス等の制御 ──────
```

- iPhone 側は TCP サーバ（ポート 5555）として待ち受けます。
- USB 接続では、Apple Devices アプリ（または iTunes）が入れる「Apple Mobile Device Service」を経由して接続します（専用ドライバー不要）。
- Wi-Fi 接続では、iPhone の画面に表示される IP アドレスへ直接接続します。
- 映像は H.264 または HEVC(H.265) のまま送られ、PC 側でデコードして仮想カメラに流します。
- 露出・ホワイトバランス・フォーカス・ズーム・ライト・背景エフェクト・色調整は PC の画面から操作できます。
- レンズは「広角」「超広角」「インカメ」から選べます。iPhone 13 のインカメはピント固定（オートフォーカスなし）で、ライトも使えません。インカメの映像は反転せずに送られ、「左右反転」の設定はインカメ用に別で記憶されます（初期値はオン）。

## 必要なもの

- GitHub アカウント（無料）
- Windows PC
- Python 3.12（exe 版を使うなら不要）
- OBS Studio 30 以上
- iTunes と iCloud（Apple 公式サイト版。AltStore 用。USB 接続の通信にも使います）、AltServer、VB-CABLE（任意）など
  - exe 版では、**初回起動のセットアップウィザードが確認して自動でインストール**します（下の「Windows側（exe 版）」を参照）。
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

**Windows の受信アプリ（exe 版）の「iPhoneのセットアップ」ウィザードが、手順を1つずつ案内します**（初回セットアップの最後に自動で開きます。あとから開くときは「セットアップ」タブの **iPhoneのセットアップ** ボタン）。各ステップには状態表示（✓ / 待機中）があり、2秒ごとに自動で更新されます。

| # | ステップ | 自動で確認できること |
|---|---|---|
| 1 | iPhoneをUSBで接続 | iPhone が USB で見つかったら ✓（見つかっていて、すでに信頼済みなら自動で次へ） |
| 2 | 「このコンピュータを信頼」 | 信頼済み（ペアリング済み）なら ✓。まだならロックを解除して「信頼」を押す |
| 3 | iTunesでWi-Fi同期をオン | 手動で確認（**iTunesを開く** ボタンあり）。7日ごとの自動更新に使うもので、手動で更新するなら省略可 |
| 4 | AltStoreをiPhoneに入れる | 手動で確認（**AltServerを起動** ボタンあり）。タスクトレイの AltServer アイコン → **Install AltStore** → このiPhone → Apple ID。サブの Apple ID で構いません |
| 5 | iPhone側の設定 | 手動で確認。**設定 > プライバシーとセキュリティ > デベロッパモード** をオンにして再起動、**設定 > 一般 > VPNとデバイス管理** で自分の Apple ID を「信頼」 |
| 6 | PhoneCamを入れる | 画面の **QRコード** を iPhone で読み取り、AltStore に PhoneCam のソースを追加して「PhoneCam」を入手（下記） |
| 7 | 接続テスト | iPhone で PhoneCam を開くと、USB 経由でつながったことを自動で確認して ✓。**完了** を押せるようになります |

どの画面でも **戻る / 次へ**、**あとで**（iPhone の準備をあとに回す）が使えます。

### PhoneCam の入れ方（AltStore のソース。アプリの更新もここから）

PhoneCam は **AltStore のソース** として配布しています。ウィザードの QR コード（ `altstore://source?url=…` ）を iPhone のカメラで読み取ると AltStore が開き、ソースを追加できます。追加後、AltStore で **PhoneCam** を入手します。新しいバージョンが出たときも、AltStore の画面から更新できます。

- ソースの URL（QR を使わず、AltStore の「Sources」に貼り付けて追加することもできます）:
  `https://github.com/syuuga101/phonecam/releases/latest/download/altstore-source.json`
- ソースは Release のたびに `altstore-source.json` として自動で作られ（`.github/scripts/make_altstore_source.py`）、常に最新の `PhoneCam.ipa` を指します。

### 別の入れ方（QR が使えないとき）

- **AltServer で直接入れる**: PC で `PhoneCam.ipa`（ウィザードの **PhoneCam.ipaをダウンロード** で「ダウンロード」フォルダに保存されます）を用意し、**Shift キーを押しながら** タスクトレイの AltServer アイコンをクリック → **Sideload .ipa…** → `PhoneCam.ipa` を選びます。この方法だと、新しいバージョンは自分で入れ直します（AltStore が入っていれば、7日の期限は AltStore が自動で更新します）。
- **iPhone に保存して AltStore で開く**: iPhone の Safari などで `PhoneCam.ipa`（Release の添付ファイル）を保存し、AltStore の **My Apps > ＋** から選びます。
- **Sideloadly**（https://sideloadly.io/）でも入れられます。PC で `PhoneCam.ipa` をドラッグして Apple ID を入力し Start するだけですが、7日ごとに自分で再インストールが必要です。

### 手動で行う場合の手順（ウィザードを使わないとき）

1. Apple の公式サイトから **iTunes** と **iCloud** をインストールします。**Microsoft Store 版は不可**です（AltServer が動きません）。
2. **AltServer** をインストールして起動します（タスクトレイに常駐します。動かないときは「管理者として実行」）。

   > 1〜2 は、受信アプリ（exe 版）の**セットアップウィザード**で自動インストールできます。手動で入れる場合は https://www.apple.com/itunes/ と https://altstore.io/ からどうぞ。
3. iPhone を USB で接続して「このコンピュータを信頼」し、iTunes で iPhone を開いて **「Wi-Fi経由でこのiPhoneと同期」** を ON にします。
4. タスクトレイの AltServer アイコン > **Install AltStore** > 自分の iPhone を選び、Apple ID を入力します。
5. iPhone の **設定 > プライバシーとセキュリティ > デベロッパモード** を ON にして再起動します。
6. iPhone の **設定 > 一般 > VPNとデバイス管理** で自分の Apple ID のアプリを開き、「信頼」します。
7. 上の「PhoneCam の入れ方」のいずれかで PhoneCam を入れます。
8. PhoneCam を起動し、カメラとローカルネットワークの許可を「許可」にします。マイクと写真（録画の保存）は、使う機能を初めて操作したときに許可を求められます。

- 7日の期限は、PC で AltServer が動いていて同じ Wi-Fi につながっている間、AltStore が自動で更新します。
- 無料の Apple ID では、AltStore を含めて同時に有効にできるサイドロードアプリは **3つまで**です。

## Windows側（exe 版・おすすめ）

1. GitHub の **Releases** から **PhoneCamReceiver.exe** をダウンロードします（この1ファイルだけです）。
2. `PhoneCamReceiver.exe` をダブルクリックします。置き場所はどこでも構いません。Windows SmartScreen の警告が出たら **詳細情報 → 実行** を選びます。
3. 初回は **セットアップウィザード** が開きます。画面の案内に沿って **次へ** を押すだけです。
   1. **ようこそ**: 内容の説明です。
   2. **必要なソフトの確認**: 入っているものは自動で「スキップ」になり、足りないものだけにチェックが付きます。**インストール** を押します。
   3. **インストール中**: ダウンロードとインストールが進みます。管理者の確認（UAC）が **1回だけ** 表示されるので **「はい」** を押します。
   4. **iPhoneの準備**: USB 接続・信頼・AltStore のインストール・PhoneCam の追加・接続テストを、7ステップで案内します（上の「iPhoneへのインストール」）。
4. 以上で完了です。OBS や Discord のカメラ一覧に「PhoneCam」が表示されます。

ウィザードが入れるもの（足りないものだけ）:

| ソフト | 用途 | 区分 |
|---|---|---|
| PhoneCamカメラ | OBS や Discord に出る専用カメラ（exe に内蔵）の登録 | 必須 |
| Visual C++ ランタイム (x64) | 専用カメラの動作に必要（Microsoft 公式） | 必須 |
| Apple USBドライバ（iTunes・Apple 公式サイト版） | USB 接続に必要。Wi-Fi だけなら不要 | USB 接続に必須 |
| iCloud（Apple 公式サイト版） | AltServer の動作に必要 | 任意（AltServer を入れるなら必要） |
| AltServer | iPhone へアプリを入れて自動更新する | 任意（おすすめ） |
| VB-CABLE | iPhone のマイクを OBS で使う（今のスピーカー／マイクは既定のまま） | 任意（「iPhoneのマイクを使う」を選んだとき） |

- ダウンロードは公式の配布元から行い、無人インストールに対応しているものは確認画面なしで入れます。VB-CABLE はドライバーの確認が Windows から表示されることがあります（「インストール」を選んでください）。
- **iTunes・iCloud・VB-CABLE などは、インストール後に PC の再起動が必要になることがあります**。その場合は完了画面に「再起動が必要です」と表示されます。
- **VB-CABLE を入れても、今のスピーカー（出力）とマイク（入力）は既定のままです。** VB-CABLE のインストーラーは Windows の「既定の音の出力先」を CABLE に切り替えてしまうことがあるため、PhoneCam は入れる前に現在の既定デバイスを控え、入れたあと（約20秒間と、次の2回の起動時）に CABLE へ切り替わっていたら元に戻します（戻したときは画面に案内を出します）。PhoneCam の受信アプリだけが「CABLE Input」へ音を出します。自動で戻せなかったときは、タスクバーのスピーカーのアイコンから出力先を元のデバイスに選び直してください。
- 自動で入れられないもの（ネットワークの問題など）は、公式ページを開いて手順を案内します。
- 後から **「セットアップ」タブ** でいつでも確認・インストールできます（**再チェック** / 行ごとの **インストール** / **初期セットアップをやり直す** / **iPhoneのセットアップ**）。
- 専用カメラの DLL は exe に内蔵されていて、登録時に `C:\ProgramData\PhoneCam\softcam` にコピーされます。登録は「接続・映像」タブの「PhoneCamカメラ」欄（**登録する（更新）** / **登録を解除**）からもやり直せます。
- 設定は `%APPDATA%\PhoneCam\phonecam_settings.json` に保存されます（旧バージョンの exe と同じフォルダにあった設定は、初回起動時に自動で引き継ぎます）。以前のバージョンから使っていて専用カメラも登録済みの場合は、ウィザードは表示されません（「セットアップ」タブから開けます）。
- **更新**: 新しい exe に置き換えるだけです。専用カメラの更新が必要なときは、起動時に案内が出ます。
- **アンインストール**: 受信アプリの「登録を解除」を押してから、exe と `%APPDATA%\PhoneCam` フォルダを削除します。
- Python は不要です。

### ウィザードを使わず手動で入れる場合

- iTunes: https://www.apple.com/itunes/ （**Microsoft Store 版ではなく**、Apple 公式サイトの「Windows版」）
- iCloud（非 Store 版）と AltServer: https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows のリンクから
- VB-CABLE: https://vb-audio.com/Cable/ （zip を展開して `VBCABLE_Setup_x64.exe` を管理者として実行し、再起動）
- Visual C++ ランタイム: https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist （x64）

## Windows側（ソースから実行）

1. `windows` フォルダの **install.bat** をダブルクリックします（初回のみ。av / numpy / pyvirtualcam をインストールします）。
2. iPhone で PhoneCam を起動し、画面を表示したままにします。
3. **start.bat** をダブルクリックすると受信アプリが開きます。
4. 「USB」を選んで **接続** を押します。Wi-Fi の場合は「Wi-Fi」を選び、iPhone の画面に表示された IP アドレス（`:5555` の前の部分）を入力して接続します。
5. 切断されても、接続中の間は 2 秒ごとに自動で再接続します。

設定は `%APPDATA%\PhoneCam\phonecam_settings.json` に自動保存され、次回起動時に復元されます。

## OBSでの使い方

1. 受信アプリで接続し、映像が流れていることを確認します（受信fps が表示されます）。
2. OBS で **ソース追加 > 映像キャプチャデバイス** を作成します。
3. デバイスに **PhoneCam** を選びます（exe 版で専用カメラの登録を済ませた場合）。

- 専用カメラ「PhoneCam」は OBS の仮想カメラとは別物なので、**OBS の「仮想カメラ開始」と同時に使えます**。Discord などでもカメラに **PhoneCam** を選びます。
- 出力先は受信アプリの「接続・映像 > 出力先」で選べます。exe 版では「PhoneCam（専用カメラ）」が既定です。
- 「OBS Virtual Camera」または「Unity Capture」を選ぶと従来どおりの仮想カメラで出力します（OBS の「仮想カメラ開始」とは同時に使えません）。
- ソースから実行する場合は、`windows` フォルダに自分でビルドした `softcam.dll`（手順は `.github/workflows/windows.yml` を参照）を置いて `register.bat` を実行すると専用カメラを使えます。無ければ OBS Virtual Camera を使います（`register.bat` / `unregister.bat` はソース実行向けです）。

## 新機能

画面は **接続・映像 / 画質調整 / エフェクト / 録画 / 音声 / 状態 / セットアップ** のタブに分かれています。

- **コーデック**（接続・映像）: H.264 と HEVC(H.265) を選べます。HEVC は同じ画質をより低いビットレートで送れるので、Wi-Fi 向きです。切り替えると自動でキーフレームを要求します。
- **背景**（エフェクト）: オフ / ぼかし / 切り抜き(緑)。強さと品質（速度優先 / バランス）を調整できます。処理は iPhone 側で行います。
  - 切り抜き(緑)は、OBS のソースに **クロマキー** フィルタ（キーの色: 緑）を追加すると背景が透明になります。
- **色調整**（エフェクト）: 明るさ・コントラスト・彩度・シャープネス。「リセット」で初期値に戻ります。
- **iPhoneの状態**（状態）: 温度・バッテリー・低電力モード・エフェクト処理時間を表示します。温度が「高い」「危険」になると、画面下に警告が出ます。その場合は fps を 30 に下げるか、エフェクトをオフにしてください。
- **専用カメラ（PhoneCam）**: 上の「Windows側」の手順で登録すると、OBS・Discord・Zoom などのカメラ一覧に「PhoneCam」が出ます。

古い設定ファイルもそのまま使えます（新しい項目は既定値になります）。

### カメラを動かすタイミング（接続・映像タブ）

使っていないときは iPhone のカメラを止めて、発熱とバッテリー消費を抑えます。接続・映像タブの「iPhoneのカメラの動作」で設定します。

| 設定 | 動き |
|---|---|
| **使われているときだけ（自動）**（既定） | OBS や Discord などが「PhoneCam」カメラを開いている間だけ動かします。使われなくなってから設定した時間（10秒 / 30秒 / 60秒）たつと止まります。 |
| **接続中ずっと** | 接続している間は常にカメラを動かします（従来と同じ）。 |
| **手動** | **開始** を押したときだけ動かします。**一時停止** で止まります。 |

- 状態は **配信中 / 待機中（使っているアプリなし） / 一時停止中 / 起動中…** と表示され、iPhone側の **カメラ: 動作中 / 停止** も見られます。
- **一時停止** を押すと、**開始** を押すまで止まったままです（自動のときも、開始を押すと自動判定に戻ります。開始の直後は、設定した時間だけ動かして映像を確認できます）。
- **自動判定は出力先が「PhoneCam（専用カメラ）」のときだけ**使えます。「OBS Virtual Camera」「Unity Capture」では使用中かどうか分からないので、「接続中ずっと」と同じ動きになります。
- カメラが止まっている間も、受信アプリとiPhoneの接続は保たれます（映像が来ないだけで、エラーにも切断にもなりません）。遅延の表示は止まります。
- 受信アプリを開いている間は、PhoneCam カメラ（黒い画面）を常に用意しておきます。これでiPhoneのカメラが止まっていても、OBS や Discord のカメラ一覧に「PhoneCam」が出て、選ぶとカメラが動き始めます（起動に1〜2秒かかります）。
- 「使われている」の判定には最大3秒ほど、カメラ起動に1〜2秒ほどかかるので、OBS で映像が出るまで少し待つことがあります。

**OBS のおすすめ設定**: 映像キャプチャデバイス（PhoneCam）のプロパティで **「表示されていないときは非アクティブにする」** をオンにすると、そのシーンを表示している間だけカメラが動きます（シーンを切り替えると、止まる）。
**Discord**: 通話で「カメラをオン」にしたときに動き始め、オフにすると止まります。

### 省電力（接続・映像タブ）

- **iPhoneの画面を真っ黒にする**: 画面を黒くして発熱とバッテリー消費を抑えます（配信は続きます）。
- **操作がないと自動で黒くする**: しない / 10秒 / 30秒 / 60秒。
- iPhone側の現在の状態も表示されます。

### 温度の詳細（状態タブ）

- カメラの負荷（正常 / やや高い / 高い / 危険 / 停止）と、その原因（本体の温度・電力の消費・深度センサーの温度・カメラの温度）。
- 送信fps、落ちたフレーム（カメラ / 通信）、エンコード時間、「高い」以上だった時間（接続してからの合計）。
- 直近30分の温度（色つきの階段線）と受信fps（灰色の細線）のグラフ。

### 遅延計測（状態タブ）

- 1秒ごとに ping を送り、iPhone と PC の時計のずれと往復時間(RTT)を推定します。
- 映像1枚ごとに **iPhone処理 / 通信 / PC処理 / 合計** を出し、直近約60フレームの平均を表示します。
- カメラの露光やOBSの表示までの時間は含みません。含めた「実際の遅延」を測るには、**計測用タイマーを開く** を押し、カメラをそのタイマーに向けて、OBSのプレビューに映るタイマーと実物のタイマーを一緒にスクリーンショットしてください。2つの差が合計の遅延です。

### iPhone側で高画質録画（録画タブ）

- 解像度（配信と同じ / 4K）、画質（10〜100 Mbps）、音声を入れる、写真アプリにも保存、を設定して **録画開始 / 録画停止** を押します。録画はiPhone内で行うので、Wi-Fiが不安定でも高画質に残せます。
- 録画中は ● 録画中の経過時間、ファイル名、サイズ、iPhoneの空き容量が表示されます。エラーは赤字で出ます。
- 4K録画中は配信用の映像を縮小して送ります。発熱が増えるので30fpsがおすすめです。
- 録画ファイルはiPhoneの「ファイル」アプリ（このiPhone内 > PhoneCam）にも残ります。
- 初めて使うときは、iPhoneでマイクと写真への保存の許可が必要です。

### iPhoneのマイク（音声タブ）

iPhoneのマイクの音を PC で鳴らし、OBS や Discord のマイクとして使えます。

1. **VB-CABLE（無料）** https://vb-audio.com/Cable/ をインストールして PC を再起動します（Windows の既定のスピーカー／マイクは変わりません。変わってしまったときは PhoneCam が元に戻します）。
2. 受信アプリの「音声」タブで **iPhoneのマイクを使う** をオンにし、出力先を **CABLE Input** にします（CABLE Input があれば最初から選ばれます）。初めて使うときは iPhone でマイクの許可が必要です。
3. OBS では **ソース追加 > 音声入力キャプチャ** で **CABLE Output** を選びます。Discord などでもマイクに「CABLE Output」を選びます。
4. 映像より音が早いときは **音声の遅延** を増やして合わせます。音量は 0〜200%、ミュートとレベルメーターもあります。

- 出力先に「CABLE Input」を選ぶと自分のスピーカーからは鳴りません。確認したいときは OBS の「音声モニタリング」を使うか、出力先をスピーカーにしてください。
- 受信アプリを起動したあとに VB-CABLE を入れた場合は、受信アプリを再起動してください。
- ソースから実行する場合は install.bat で `sounddevice` が入ります。入っていないと音声タブは使えません（映像は使えます）。

## おすすめ設定

- 接続: **USB**（Wi-Fi より安定し、遅延も小さい）
- 映像: **1080p / 60fps / ビットレート 20Mbps**
- 向き（映像 > 向き）: **自動**（iPhone を縦に持てば縦映像、横に持てば横映像）／**横**（常に 1920×1080）／**縦**（常に 1080×1920）。iPhone を回転すると映像サイズが変わりますが、仮想カメラの出力サイズは固定されるので OBS の映像は止まりません（下の「出力の形」「合わせ方」）。OBS のキャンバスを縦にするなら「縦」固定がおすすめです。
- 出力の形（映像 > 出力の形）: 仮想カメラのサイズを固定します。**向きの設定に合わせる**（既定。向きが「縦」なら縦長、それ以外は横長）／**横長で固定**／**縦長で固定**／**映像のサイズそのまま**（向きが変わると出力サイズも変わり、OBS でソースの再読み込みが必要）。サイズは解像度の設定に従います（1080p なら 1920×1080 / 1080×1920）。
- 合わせ方（映像 > 合わせ方）: 出力の形と映像の向きが違うときの収め方です。**余白を付けて全体を表示**（既定。余白は黒、背景「切り抜き(緑)」のときは緑）／**はみ出す部分を切り取って埋める**（中央を切り出す）。
- なぜ固定するのか: OBS は仮想カメラの最初のサイズを覚えるため、途中でサイズが変わると映像が止まります。出力サイズを固定して映像をその中に収めることで、向きが変わっても止まらなくなります。出力サイズ（解像度・出力の形・向き）を変えた場合だけ、OBS でソースを一度非表示→表示にしてください。
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
| 仮想カメラを開始できない | OBS Virtual Camera の場合: OBS Studio 30以上をインストールし、OBS の「仮想カメラ開始」が ON なら停止してください。 |
| 「PhoneCam」がカメラ一覧に出ない／「未登録」と出る | 受信アプリの「接続・映像」タブにある「PhoneCamカメラ」の **登録する（更新）** を押してください（管理者の確認で「はい」）。ソース実行の場合は register.bat を使います。 |
| PhoneCam に切り替えたのに OBS で映像が出ない | 受信アプリで接続して映像が流れている必要があります。OBS のソースを開き直してください。 |
| 映像が固まる／乱れる | 受信アプリが自動でキーフレームを再要求します。改善しない場合はビットレートを下げるか USB を使ってください。 |
| 映像が暗い／ちらつく | 露出をマニュアルにし、シャッター速度を地域に合わせてください（上記参照）。 |
| 数日後にアプリが開かなくなった | 無料 Apple ID は7日で期限切れです。AltStore なら PC の AltServer を起動して同じ Wi-Fi につなぎ、My Apps で更新してください（Sideloadly の場合は再インストール）。 |
| 画面の文字や行が切れる（高DPI） | 受信アプリは高DPIに対応しています。ウィザードは大きさを変えられ、中身はスクロールできます。それでも切れる場合は、ウィンドウを広げてください。 |
| VB-CABLE を入れたら音が出なくなった | タスクバーのスピーカーのアイコンから、出力先を元のスピーカー／ヘッドホンに戻してください（PhoneCam も自動で戻します）。 |
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

## 通信プロトコル（開発者向け）

TCP 上のメッセージは `[種別 1byte][長さ 4byte ビッグエンディアン][本体]` です。

| 種別 | 方向 | 内容 |
|---|---|---|
| 1 | iPhone → PC | H.264 アクセスユニット |
| 2 | iPhone → PC | HEVC アクセスユニット（Annex B。キーフレームには VPS/SPS/PPS を付加） |
| 3 | iPhone → PC | 状態 JSON |
| 4 | iPhone → PC | pong（JSON） |
| 5 | iPhone → PC | 映像1枚ごとの時刻情報（16byte、次の映像パケットの直前に送る） |
| 6 | iPhone → PC | マイク音声 |
| 10 | PC → iPhone | 制御 JSON |

- PC 側はデコーダーを1つだけ保持し、受信した種別のコーデックが現在と違えばデコーダーを作り直し、キーフレームを要求します。
- 制御 JSON の追加キー:
  - `"codec"`: `"h264"` / `"hevc"`
  - `"background"`: `{"mode": "off"|"blur"|"green", "strength": 0〜1（既定0.6）, "quality": "fast"|"balanced"}`
  - `"color"`: `{"brightness": -0.5〜0.5（0）, "contrast": 0.5〜1.5（1）, "saturation": 0〜2（1）, "sharpness": 0〜1（0）}`
- 状態 JSON の追加キー: `codec`, `background`, `color`, `thermalState`（`nominal`/`fair`/`serious`/`critical`）, `batteryLevel`（0〜1、不明は -1）, `batteryState`（`charging`/`full`/`unplugged`/`unknown`）, `lowPowerMode`（bool）, `processingMs`（float）

### v1.3 の追加（すべて追加のみ。未知の種別・キーは両側とも無視します）

- 種別 4: `{"pong": <PCが送ったpingの値>, "t": <iPhoneのホスト時計[秒]>}`
- 種別 5: `float64 BE captureTime` + `float64 BE sendTime`（iPhoneのホスト時計[秒]。キャプチャ時刻と、映像パケットをネットワークへ渡した時刻）
- 種別 6: `float64 BE captureTime` + `uint32 BE sampleRate` + `uint8 channels`（常に1）+ `int16 リトルエンディアン PCM`
- 遅延の計算: ping を `perf_counter` のミリ秒で送り、`offset = iPhone時刻 - (PC送信時刻 + RTT/2)`（直近15回のうち RTT 最小のもの）。`iPhone処理 = sendTime - captureTime`、`通信 = (PC受信時刻 + offset) - sendTime`、`PC処理 = 仮想カメラへ渡し終わった時刻 - PC受信時刻`。
- 制御 JSON の追加キー:
  - `"ping"`: 数値（そのまま pong で返る）
  - `"mic"`: `{"enabled": bool}`（既定 false）
  - `"screen"`: `{"black": bool, "autoBlackSeconds": 0|10|30|60}`
  - `"recordSettings"`: `{"resolution": "same"|"4k", "bitrate": 10〜100（Mbps、既定50）, "includeAudio": bool（既定true）, "saveToPhotos": bool（既定true）}`
  - `"record"`: `"start"` / `"stop"`
  - `"stream"`: `{"active": bool}`。false のとき iPhone はエンコードを止め、録画中・プレビュー表示中でなければカメラも止めます（接続・状態・ping は続きます）。新しい接続では iPhone は既定で true として扱うので、PC は接続直後（"all" の制御に含めて）に希望の値を送ります。
- 状態 JSON の追加キー: `pressureLevel`（`nominal`/`fair`/`serious`/`critical`/`shutdown`）, `pressureFactors`（`systemTemperature`/`peakPower`/`depthModuleTemperature`/`cameraTemperature` の配列）, `sentFps`, `cameraDroppedFrames`, `networkDroppedFrames`, `encodeMs`, `screenBlack`, `autoBlackSeconds`, `mic`（`{enabled, available, sampleRate}`）, `recording`（`{active, seconds, fileName, bytes, freeBytes, resolution, error}`）, `recordSettings`, `streamActive`（bool）, `cameraRunning`（bool。カメラが実際に動いているか）

## 専用カメラのしくみ（開発者向け）

[softcam](https://github.com/tshino/softcam)（MIT）v1.8.1 を、GitHub Actions で表示名「PhoneCam」・独自の CLSID・独自の共有メモリ名に書き換えてビルドしています（他の softcam 利用アプリと衝突しません）。受信アプリは（exe 版では内蔵し `%ProgramData%\PhoneCam\softcam\<ハッシュ>\` に登録した）`softcam.dll` を ctypes で読み込み、BGR24 のフレームを渡します。CLSID は `windows/phonecam_receiver.py` の `PHONECAM_CLSID` と `.github/workflows/windows.yml` で一致させてください。

### 使用中の検出（開発者向け）

softcam の共有メモリ（`PhoneCam/SharedMemory`）の先頭ヘッダ `{u32 image_offset, u16 width, u16 height, f32 framerate, u8 is_active, u8 connected_min_version, u8 sender_heartbeat, u8 receiver_heartbeat, u64 frame_counter}` のうち、`receiver_heartbeat`（15 byte 目）を受信アプリが読み取り専用で 0.5 秒ごとに見ます（共有メモリは作らず、開くだけ）。softcam を使うアプリ側が約20msごとに +1 するので、3秒以内に値が変わっていれば「使われている」と判定します（8bit で一周するので、前回値との不一致だけを見ます）。送る側（受信アプリ）の `sender_heartbeat` も映像の有無と無関係に動くため、映像が止まっていても使う側は繋がったままです。出力サイズ（解像度・出力の形・向き）を変えてカメラを作り直したときだけ、使う側の再読み込みが必要です。
