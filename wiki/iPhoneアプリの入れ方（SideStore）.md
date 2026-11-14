# iPhoneアプリの入れ方（SideStore）

PhoneCam は App Store には無いので、**SideStore**（サイドストア）という、iPhone 用の「アプリの入れ物」を使って入れます。Mac は不要です。

SideStore とは、自分の Apple ID でアプリに署名（「この人が入れたアプリです」という印をつけること）して入れる、無料のアプリです。**PC とつなぐのは、最初の 1 回だけ**です。そのあとの更新は、iPhone だけでできます。

> 以前 AltStore（と PC の AltServer）で入れていた人は、[AltStore から乗り換える](#altstore-から乗り換える) へ。

## 用意するもの

**PC**

- Windows 8 以上の 64bit
- **iTunes**（Apple 公式サイト版をおすすめします）。iPhone を USB で見つけるための部品が入ります
- **iloader**（アイローダー。SideStore を iPhone に入れる PC 用のソフト）。インストーラーは MSI 版をおすすめします（ダウンロード先は [SideStore 公式の準備ページ](https://docs.sidestore.io/docs/installation/prerequisites) からたどれます）
- USB ケーブル

**iPhone**

- **パスコード**を設定してあること（設定 > Face ID とパスコード、など）
- iOS 15 以上（PhoneCam 自体は iOS 17 以上が必要です）
- App Store の **LocalDevVPN**（ローカルデブ ブイピーエヌ。無料）をインストール済みであること。SideStore が iPhone の中で通信するために使う VPN で、外のサーバーにはつながりません

**そのほか**

- Apple ID（無料のものでOK。サブのアカウントでも構いません）
- **Wi-Fi**（更新するときに使います。モバイル回線では更新できません）

## 手順

所要時間は 15〜30 分ほどです。**手順 1〜2 だけ PC を使い、手順 3 からは iPhone だけ**です。

### 1. iPhone を PC につなぐ

1. USB ケーブルで iPhone と PC をつなぎます。
2. iPhone のロックを解除します。「このコンピュータを信頼しますか？」と出たら **信頼** を押して、パスコードを入力します。

### 2. iloader で SideStore を入れる

1. PC で **iloader** を開きます。
2. **Apple ID** でサインインします（メールアドレスとパスワード。**大文字と小文字は区別されます**）。
3. つないだ iPhone を選びます。
4. **Install SideStore (Stable)** を押します。
5. 終わるまで待ちます。iPhone のホーム画面に SideStore が出ます。

> **Stable（安定版）** を選んでください。Nightly（最新の開発版）は不安定なことがあります。

### 3. iPhone で SideStore を「信頼」する

1. iPhone の **設定** を開き、**一般** > **VPNとデバイス管理**（英語表示では VPN & Device Management）に進みます。
2. 「デベロッパApp」（Developer App）の下に、自分の Apple ID の名前が出ています。それを押します。
3. **「（Apple ID）を信頼」**（Trust）を押し、**Allow & Restart**（許可して再起動）を押します。
4. パスコードを入力して確定します。

### 4. デベロッパモードをオンにする

1. **設定** > **プライバシーとセキュリティ**（Privacy & Security）を開きます。
2. いちばん下までスクロールして、**デベロッパモード**（Developer Mode）をオンにします。
3. iPhone が再起動します。再起動後に出る確認で「オンにする」を選び、パスコードを入力します。

> 「デベロッパモード」が出ないときは、[[よくある質問とトラブル]] の「デベロッパモードが出ない」を見てください。

### 5. LocalDevVPN をつなぐ

1. **LocalDevVPN** を開きます。
2. **Connect** を押します。画面の上に VPN のマークが出ます。
3. iPhone を **Wi-Fi** につないでおきます（モバイル回線はだめです）。

### 6. SideStore にサインインして、更新する

1. **SideStore** を開きます。
2. 手順 2 の iloader と **同じ Apple ID** でサインインします。
3. 下のタブの **My Apps** を開きます。
4. **SideStore** の右に **7 DAYS**（あと 7 日、という意味）と出ています。それを押して、手動で更新します。
5. 「署名証明書を失効させるか、新しく作るか」という確認が出たら、**Yes** または **Refresh Now** を押します。

うまくいくと、ホーム画面に戻って通知が出ます。ここまでで、SideStore の準備は完了です。**PC は外して大丈夫です。**

> **サインインしてすぐは、アプリを入れる前にこの更新をしてください。** iloader が入れた SideStore と、SideStore 自身では内部の ID が違うことがあり、あとから更新すると、その前に入れたアプリが消えることがあります。

### 7. PhoneCam を入れる

PhoneCam は、SideStore の **ソース**（アプリの配布元のリスト）として配っています。

1. iPhone の **SideStore** を開き、下のタブの **Sources** を開きます。
2. **＋** ボタンを押します。
3. 次の URL を貼り付けて、追加します。

```
https://github.com/syuuga101/phonecam/releases/latest/download/sidestore-source.json
```

> 同じ内容の `altstore-source.json`（AltStore 用の同じ内容のファイル。旧 URL も引き続き使えます）もあります。

4. 追加したソースを開き、**PhoneCam** の入手ボタンを押して入れます。
5. 終わったら、PhoneCam を開きます。

URL を打つのが大変なときは、このページの URL をコピーして、iPhone に送ってください（メモアプリやメッセージなど）。

#### ソースが使えないときの別の方法

- iPhone の Safari などで、[Releases](https://github.com/syuuga101/phonecam/releases/latest) の `PhoneCam.ipa` を保存します。SideStore の **My Apps** を開き、**＋** から、保存した `PhoneCam.ipa` を選びます。新しいバージョンは自分で入れ直します。

### 8. 最初の起動

PhoneCam を開いたら、カメラとローカルネットワークの許可を「許可」にします。マイクと写真（録画の保存）は、使う機能を初めて操作したときに許可を求められます。

そのあと、PC の受信アプリを起動して、USB でつなぐと、映像が流れます（[[はじめに（セットアップ）]]）。

## 知っておくこと

- **7 日で期限が切れます**（無料の Apple ID の場合）。SideStore が、iPhone の中で自動で更新します。うまくいかないときは、手動で更新します（[[アップデート方法]] の「7 日ごとの更新」）。
- **更新するときは、LocalDevVPN を Connect にして、Wi-Fi につないでおきます。**
- 無料の Apple ID で同時に有効にできるアプリは **3 つまで**です（SideStore 自身も 1 つに数えます）。PhoneCam を入れると、残りは 1 つです。
- 1 週間に入れられる「別々のアプリ」は 10 種類までです。
- iPhone の iOS を更新したり、iPhone を初期化したりすると、**ペアリングファイル**（iPhone と PC が「お互いを知っている」という印のファイル）が使えなくなることがあります。そのときは、PC の **iloader** で入れ直します（[[よくある質問とトラブル]] の「ペアリングファイルの期限切れ」）。

## AltStore から乗り換える

これまで **AltStore**（と PC の **AltServer**）で PhoneCam を入れていた人は、次の 3 つをします。AltStore の更新は PC が必要でしたが、SideStore は iPhone だけで更新できるので、乗り換えるとラクになります。

### 1. SideStore を入れる

上の [手順 1〜6](#手順) のとおりに、SideStore を入れます。AltStore はそのままで構いません（手順の途中で消す必要はありません）。

> 無料の Apple ID で同時に入れられるのは **3 アプリまで**です。AltStore + PhoneCam + SideStore で、すでに 3 つ使っていると、SideStore が入りません。そのときは、先に AltStore を消して（下の「3.」）から、SideStore を入れてください。

### 2. PhoneCam を SideStore から入れ直す

1. SideStore の **Sources** に、PhoneCam の配布元を追加します（上の [手順 7](#7-phonecam-を入れる)）。
2. PhoneCam を入れます。AltStore で入れた PhoneCam が残っている場合、SideStore から入れると、**消さずにそのまま上書き**されて、設定も残ります。

   > 上書きされないで、2 つ並んだり、エラーになったりしたときは、AltStore 側の PhoneCam を一度消してから入れ直してください（PhoneCam の設定は iPhone の中のものなので、消すと戻ります）。

3. 入れた PhoneCam を開いて、PC の受信アプリとつながることを確かめます。

### 3. AltStore と AltServer を消してよい

SideStore から PhoneCam が動くことを確かめたら、次を消して構いません。

- **iPhone**: ホーム画面の **AltStore** を長押しして、アプリを削除します。
- **PC**: AltServer をアンインストールします（設定 > アプリ から）。iTunes は、PhoneCam が USB でつなぐのに使うので、**残してください**。iCloud は、AltServer のためだけに入れたのなら、消して構いません。

これで、PC が起動していなくても、PhoneCam の更新ができるようになります。
