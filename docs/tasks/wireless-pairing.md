# タスク: Wi-Fi 接続を増やす（QR ペアリング・自動検出・テザリング／ホットスポット）（Codex向け）

## 背景
今の Wi-Fi 接続は、iPhone の画面の IP アドレスとペアリングコードを PC に手で入れ、PC から iPhone:5555/5556 へ TCP でつなぐ方式。
同じ Wi-Fi でも端末どうしの通信が遮断されている環境（大学の Wi-Fi など）ではつながらない。
仕組みは docs/ARCHITECTURE.md と docs/PROTOCOL.md、ペアリングコードは ios/PhoneCam/Security.swift と windows/phonecam/core/settings.py（pair_code）を参照。
Tailscale 対応は今回やらない。

## やること

### 1. QR コードでペアリング（新しい接続方法）
- PC（Qt の接続ページ）に「QRコードでつなぐ」を追加し、QR を表示する。
- QR の中身は `phonecam://pair?...` 形式にする。含めるものは次のとおり。
  - PC の到達候補 IP（有効な IPv4 を全部）
  - 待ち受けポート
  - 一回限りのトークン（数分で失効）
  - PC 名
  - プロトコル版
- iPhone アプリに「QRコードを読み取る」を追加する（AVCaptureMetadataOutput。今のカメラセッションと競合しないよう、読み取り中は配信を止めてよい）。
- 読み取ったら、iPhone から PC の待ち受けポートへ接続し、次を送る。
  - トークン
  - iPhone の IP 一覧
  - ペアリングコード
  - 端末名・識別子
- PC はトークンを照合して「つないだ端末」として保存し（設定に known_devices、複数可）、以後は今までどおり PC→iPhone で接続する。2回目以降は QR 不要。
- PC の待ち受けは QR を表示している間だけ開く。初回は Windows ファイアウォールの許可が出るので、案内を出す。
- QR 生成は Python の `qrcode`（requirements.txt と PyInstaller に追加）。もしくは自前で十分小さく実装する。

### 2. 自動検出（IP を入れなくて済むように）
- iPhone は Wi-Fi 待ち受け中、Bonjour（NWListener の service、`_phonecam._tcp`）で自分を知らせる。TXT に端末名・識別子・版を入れる。Info.plist に NSBonjourServices と NSLocalNetworkUsageDescription（InfoPlist.xcstrings の ja/en）を入れる。
- PC は `zeroconf` で見つけて、接続ページに候補として並べる（known_devices を優先表示）。
- Bonjour が通らない環境のために、次の場合は自動で候補を足す。
  - **iPhone のインターネット共有（テザリング）**: PC のデフォルトゲートウェイが 172.20.10.1 で、その IP の 5555 が開いていれば候補に足す。
  - **PC のモバイルホットスポット**: 192.168.137.0/24 の ARP 表、または短いポートスキャンで、5555 が開いている端末を候補に足す。

### 3. 案内の UI
- 接続ページの Wi-Fi まわりに「つなぎ方」を1行で選べるようにする。
  - 同じ Wi-Fi
  - iPhone のテザリング
  - PC のホットスポット
  - QR コード
- 端末どうしの通信が遮断されている Wi-Fi でつながらないときは、テザリング／ホットスポットを勧めるヒントを出す（つながらないまま一定時間経ったときだけ）。
- PC のホットスポットは、Windows の設定画面（`ms-settings:network-mobilehotspot`）を開くボタンでよい。
- 文言は windows/locales/ja.json と en.json の両方、iOS は Localizable.xcstrings の ja/en。

## 守ること
- USB 接続と、今の手入力の Wi-Fi 接続は今までどおり動くこと。
- ペアリングコードの照合は今までどおり iPhone 側で行う（QR は補助で、認証を弱めない）。
- 始める前に `git merge origin/main` で最新を取り込む。

## 確認
- Windows で exe をビルドし、iPhone 実機で次のつなぎ方を試す。
  - 同じ Wi-Fi + QR
  - 自動検出
  - iPhone のテザリング
  - PC のホットスポット
- iOS は CI（Build iOS）の成功で確認する。この PC では iOS をビルドできないので、AltStore で IPA を入れて試す。
- 確認できたこと・できなかったことを PR の説明に日本語で書く。
