# 通信プロトコル

iPhone アプリ（サーバ）と Windows 受信アプリ（クライアント）の間の通信仕様です。

- 変更はすべて「追加のみ」です。**未知の種別・未知のキーは、両側とも無視します**（古いアプリとも動くようにするため）。
- 表の「v1.3」「v1.4」はプロトコルの改訂番号で、アプリのバージョンとは一致しないことがあります（v1.3 はアプリ 1.3.0、v1.4 の音声チャンネルはアプリ 1.3.6 で入りました）。
- 「v1.4.0以降」と書いてある項目（セキュリティの認証・録画の追加分）は、アプリ v1.4.0 で入ります。

## 接続

| ポート | 用途 |
|---|---|
| 5555 | メインの接続（映像・状態・制御・ping） |
| 5556 | 音声専用チャンネル（プロトコル v1.4） |

- iPhone は TCP サーバとして待ち受けます。
- USB: Apple Mobile Device Service（usbmux）経由で、同じデバイス ID に対してポートごとに Connect します。
- Wi-Fi: iPhone の画面に表示される IP アドレスへ直接 TCP 接続します。
- TCP_NODELAY を使います。

## フレーミング

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

## iPhone → PC のパケット

### 種別 4: pong（JSON）

`{"pong": <PCが送ったpingの値>, "t": <iPhoneのホスト時計[秒]>}`

ping を受け取ったらすぐに返します（キューに溜まった映像より先に送ります。状態と同じ優先度で、捨てません）。

### 種別 5: 映像の時刻情報（16byte）

`float64 BE captureTime` + `float64 BE sendTime`

- iPhone のホスト時計[秒]です。ホスト時計は `CMClockGetTime(CMClockGetHostTimeClock())` で、`captureTime` はカメラのサンプルバッファの PTS（`CMTimeGetSeconds`）、`sendTime` は映像パケットをネットワークへ渡した時刻です。
- 実際に送る映像パケットの直前に、同じキューで送ります（順序が保証されます）。捨てたフレームには付きません。

### 種別 6: マイク音声

`float64 BE captureTime`（最初のサンプルの時刻） + `uint32 BE sampleRate` + `uint8 channels`（常に 1。iPhone がモノラルにします） + `int16 リトルエンディアン PCM`

- 取得したバッファをそのまま送ります（通常は約 20ms。低遅延経路では約 5ms）。
- 映像の間引きロジックでは捨てません。ただし送信キューに溜まった音声が上限を超えたら、止まらずに古い方を捨てます（メイン 5555 では約 200ms、音声チャンネル 5556 では約 60ms）。

### 種別 3: 状態 JSON

基本のキー: `codec`, `background`, `color`, `thermalState`（`nominal`/`fair`/`serious`/`critical`）, `batteryLevel`（0〜1、不明は -1）, `batteryState`（`charging`/`full`/`unplugged`/`unknown`）, `lowPowerMode`（bool）, `processingMs`（float）

| 追加された版 | キー |
|---|---|
| v1.3 | `pressureLevel`（`nominal`/`fair`/`serious`/`critical`/`shutdown`。アクティブなカメラの `systemPressureState.level`）, `pressureFactors`（`systemTemperature`/`peakPower`/`depthModuleTemperature`/`cameraTemperature` の配列）, `sentFps`, `cameraDroppedFrames`（接続してからの数）, `networkDroppedFrames`（同）, `encodeMs`（移動平均）, `screenBlack`, `autoBlackSeconds`, `mic`（`{enabled, available, sampleRate}`）, `recording`（`{active, seconds, fileName, bytes, freeBytes, resolution, error}`。エラーが無いときの `error` は空文字）, `recordSettings`（現在値の返信） |
| v1.4 | `streamActive`（iPhone が持っている現在値）, `cameraRunning`（`AVCaptureSession.isRunning`。カメラが実際に動いているか）, `audioChannel`（音声チャンネルが繋がっているか）, `micPath`（iPhone のマイク経路。`lowLatency` = 低遅延エンジン経路、`capture` = 通常のキャプチャ経路） |
| v1.4.0以降 | `wifiAllowed`（Wi-Fi 接続を許可しているか）, `authRequired` / `authOk`（認証。下記）, `recording.droppedFrames`, `cameraDropReasons`（`{"late": n, "outOfBuffers": n, "discontinuity": n}`） |

- `streamActive` と `cameraRunning` は、どちらかが変わったときにすぐ状態を送ります。カメラが止まっている間は、露出や ISO などカメラ由来の値が古いままのことがあり、映像・時刻情報のパケットも来ません。

## PC → iPhone の制御（種別 10, JSON）

| キー | 内容 | 追加された版 |
|---|---|---|
| `"codec"` | `"h264"` / `"hevc"` | 基本 |
| `"background"` | `{"mode": "off"\|"blur"\|"green", "strength": 0〜1（既定0.6）, "quality": "fast"\|"balanced"}` | 基本 |
| `"color"` | `{"brightness": -0.5〜0.5（0）, "contrast": 0.5〜1.5（1）, "saturation": 0〜2（1）, "sharpness": 0〜1（0）}` | 基本 |
| `"ping"` | 数値（PC が決める値。例: perf_counter のミリ秒）。そのまま pong で返ります | v1.3 |
| `"mic"` | `{"enabled": bool}`（既定 false）。オンにすると iPhone が既定の音声入力をセッションに加えます。音声は、クライアントが繋がっている間だけ送ります | v1.3 |
| `"screen"` | `{"black": bool, "autoBlackSeconds": 0\|10\|30\|60}`（省電力） | v1.3 |
| `"recordSettings"` | 録画の設定（下記）。ほかの設定と同じようにマージして保存されます | v1.3 |
| `"record"` | `"start"` / `"stop"` | v1.3 |
| `"stream"` | `{"active": bool}`（下記） | v1.4 |
| `"auth"` | `{"code": "123456"}`（下記） | v1.4.0以降 |

### recordSettings

`{"resolution": "same"|"4k", "bitrate": 10〜100（Mbps、既定50）, "includeAudio": bool（既定true）, "saveToPhotos": bool（既定true）}`

v1.4.0以降 の追加:

- `"preset"`: `"standard"` / `"high"` / `"max"`（既定 `standard`）
- `"container"`: `"mp4"` / `"mov"`（既定 `mp4`）
- `"bitrate"` が無いときは、iPhone がプリセットからビットレートを決めます（HEVC）。

| プリセット | 1080p | 4K |
|---|---|---|
| standard | 10 Mbps | 25 Mbps |
| high | 16 Mbps | 40 Mbps |
| max | 25 Mbps | 60 Mbps |

- 明示的な `"bitrate"` があれば、そちらが優先されます。

### stream（カメラを動かすタイミング）

使っていないときに iPhone のカメラを止めて、発熱とバッテリー消費を抑えるための仕組みです。

- `false` のとき、iPhone はエンコードを止め（エンコーダーを破棄）、録画中・iPhone 側のプレビュー表示中でなければカメラ（マイクも）を止めます。TCP 接続・状態（1 秒ごと）・ping/pong は続きます。
- **既定は true**: 値は接続ごとにしか保持されません。新しいクライアントが繋がるたびに iPhone は `streamActive` を true として扱うので、"stream" を送らない古い PC アプリは従来どおり動きます。新しい PC アプリは、接続直後（"all" の制御に含めて）に希望の値を送ります。
- `true` になったとき、iPhone はキーフレームを要求し、IDR（パラメータセット付き）を送るまでフレームを捨てます。再開後の最初の映像パケットは必ずキーフレームです。
- 映像・音声は `接続中 && streamActive` の間だけ送ります（音声はさらに mic が有効なとき）。
- カメラが動くのは、`(接続中 && streamActive) || 録画中 || iPhone 側のプレビューがオン` のときです。停止中に録画を始めると、カメラは自動で動き始めます。

## 音声チャンネル（ポート 5556、v1.4）

映像と同じ通信路だと、大きな映像のうしろで音が待たされて遅れます。そのため音声は別の接続で送ります。

- iPhone はポート **5556** でも待ち受けます（フレーミングは同じ）。PC は、5555 に繋いだ直後に、USB なら同じデバイス ID で usbmux の Connect（ポート 5556）、Wi-Fi なら直接 TCP で 5556 に繋ぎます（TCP_NODELAY、SO_RCVBUF 64KB）。
- 音声チャンネルとメインの接続が**両方**あるとき、iPhone は種別 6 を**音声チャンネルだけ**で送ります。音声チャンネルが無いときは従来どおり 5555 で送るので、古い iPhone アプリ・古い PC アプリとも互換です（PC は両方の経路の種別 6 を処理します）。
- PC は 5556 に何も送りません（任意で、種別 10 の `{"hello": "audio"}`。iPhone は無視してかまいません）。
- iPhone は音声クライアントを 1 つだけ受け付けます（新しい接続が古い接続を置き換えます）。メインの接続が閉じたら音声チャンネルも閉じます。PC は、音声チャンネルが繋がらなければ 2 秒ごとに繋ぎ直します。
- 音声チャンネルのソケットは TCP_NODELAY で、iPhone は未送信の音声を約 60ms までしか溜めません（それを超えたら新しい方を捨てます）。

## 認証（v1.4.0以降）

接続の許可についての仕様です。

### USB と Wi-Fi の区別

usbmux 経由の接続は、iPhone から見るとループバックアドレス（127.0.0.1 / ::1）から来た接続として見えます。**ループバックのクライアントは信頼します**（USB 接続には、iPhone 側で「このコンピュータを信頼」が必要なため）。

### Wi-Fi

- Wi-Fi の待ち受けは**既定でオフ**です。iPhone アプリの「Wi-Fiからの接続を許可」で切り替えます（設定は保存されます）。オフのとき、ループバック以外の接続はすぐに閉じます。
- Wi-Fi を許可しているとき、ループバック以外のクライアントは最初に認証します。iPhone は **6 桁のペアリングコード**（ランダム。UserDefaults に保存。「コードを変更」ボタンで変更）を表示します。
- クライアントは、**5 秒以内**に、最初の種別 10 メッセージとして `{"auth": {"code": "123456"}}` を送ります。
- 認証が済むまで、iPhone は状態を 1 つ（`{"authRequired": true, "authOk": false}`）だけ送り、ほかの制御はすべて無視します。映像・音声・時刻情報・pong も送りません。
- コードが違う、または時間切れのときは接続を閉じます。同じ IP から **3 回**失敗すると、その IP を **60 秒間**拒否します。
- 成功すると、通常の状態（`"authOk": true`）を送ります。
- ループバックのクライアントが `{"auth": ...}` を送ってきても無視します（常に OK）。
- 音声チャンネル（5556）は、ループバックからの接続か、現在認証済みのメイン接続と同じ IP からの接続だけ受け付け、それ以外は閉じます。

### PC 側の動き

Wi-Fi モードには「ペアリングコード」欄があります（保存されます）。接続した直後（"all" の制御より前）に auth メッセージを送ります。iPhone が `authRequired` かつ `!authOk` と返したり、接続を閉じたりしたら、「ペアリングコードが違います。iPhoneの画面に表示されている6桁のコードを入力してください」と表示します。

> 認証は「同じネットワーク上の知らない人が勝手に繋がらないようにする」ためのものです。Wi-Fi 上の通信自体は暗号化されません。詳しくは [SECURITY.md](../SECURITY.md)。

## 遅延の計算

- PC は 1 秒ごとに `ping` を `perf_counter` のミリ秒で送ります。
- 時計のずれ: `offset = iPhone時刻 - (PC送信時刻 + RTT/2)`（直近 15 回のうち RTT が最小のもの）
- `iPhone処理 = sendTime - captureTime`
- `通信 = (PC受信時刻 + offset) - sendTime`
- `PC処理 = 仮想カメラへ渡し終わった時刻 - PC受信時刻`
- 合計 = 3 つの合計。カメラの露光や OBS の表示までの時間は含みません。
