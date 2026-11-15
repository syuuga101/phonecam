# iPhoneアプリの入れ方（SideStore）

PhoneCam は App Store には無いので、**SideStore**（サイドストア）というアプリを使って iPhone に入れます。このページは、**初めてサイドロードする人**向けに、1 つずつ順番にやれば入るように書いています。Mac は要りません。

> **サイドロード** とは、App Store 以外の方法でアプリを入れることです。自分の Apple ID でアプリに「署名」（「この人が入れたアプリです」という印をつけること）をして入れます。iPhone を改造（脱獄）する必要はありません。

PhoneCam の PC 側の **セットアップウィザード**（受信アプリの初回画面）も、同じ **8 段階** で案内します。ウィザードを見ながら、このページを読んでもかまいません。

---

## 全体の流れ

所要時間は、**初めてなら 30〜40 分**ほどです（ダウンロードや再起動の待ち時間を含む目安です）。準備と手順 1〜3 は PC を使います。手順 4〜7 は iPhone で操作し、手順 8 で PC の受信アプリにつなぎます。

| 段階 | やること | 使うもの | 目安 |
|---|---|---|---|
| 0 | 準備: PC にソフト（iTunes・iloader）を入れる | PC | 10 分 |
| 1 | iPhone を USB で PC につなぐ | PC・iPhone | 1 分 |
| 2 | 「このコンピュータを信頼」を押す | iPhone | 1 分 |
| 3 | **iloader** で **SideStore** を iPhone に入れる | PC | 5 分 |
| 4 | デベロッパ App を信頼して、デベロッパモードをオンにする | iPhone | 5 分 |
| 5 | **LocalDevVPN** を入れて、Connect する | iPhone | 3 分 |
| 6 | SideStore にサインインして、最初の更新をする | iPhone | 5 分 |
| 7 | SideStore に配布元を追加して、**PhoneCam** を入れる | iPhone | 3 分 |
| 8 | 接続テスト（PC の受信アプリとつながるか確認） | PC・iPhone | 2 分 |

> PC を使うのは **最初の 1 回だけ**です。あとの更新は、iPhone だけでできます（[[アップデート方法]]）。

---

## 用意するもの

次をそろえてから始めてください。□ にチェックをつけながら確認すると楽です。

**PC**

- [ ] Windows 10 または 11 の PC（このガイドでは **x64** 版を使います）
- [ ] iPhone をつなぐ **USB ケーブル**（充電専用ではなく、データ通信ができるもの。iPhone に付属のケーブルで大丈夫です）
- [ ] インターネットにつながっていること

**iPhone**

- [ ] iOS 17 以上（PhoneCam が必要とする版です）
- [ ] **パスコード** を設定してあること（設定 > Face ID とパスコード、など）
- [ ] **Wi-Fi** につながること（モバイル回線では、SideStore の更新ができません）

**そのほか**

- [ ] **Apple ID**（無料のものでOK。メールアドレスとパスワードを控えておきます。サブのアカウントでも構いません。パスワードは **大文字と小文字が区別されます**）
- [ ] Apple ID の **確認コード** を受け取れるもの（Apple ID でサインイン済みのほかの Apple 端末、または登録してある電話番号の携帯）

> 無料の Apple ID には、制限があります。**アプリは同時に 3 つまで**（SideStore 自身も 1 つに数えます）、**7 日ごとに更新が必要**です。このページの手順は、その範囲で、いちばん簡単に PhoneCam を使える方法です。くわしくは [無料の Apple ID の制限](#無料の-apple-id-の制限) へ。

---

## 手順 0: PC に iTunes と iloader を入れる

PC に、**iTunes**（iPhone を USB で見つけるための部品が入ります）と、**iloader**（アイローダー。SideStore を iPhone に入れる PC 用のソフト）を入れます。

> **PhoneCam のウィザードを使うと、自動で案内されます。** 「必要なソフトの確認」で、iTunes と iloader にチェックが付いていれば、そのまま **インストール** を押すだけです（[[はじめに（セットアップ）]]）。ここでは、自分で入れる場合を説明します。

**0-1. Windows が 64bit か確認します（念のため）**

1. Windows の **設定** を開きます。
2. **システム** を選びます。
3. **バージョン情報** を開きます。
4. 「システムの種類」を確認します。

> 📷 画像: images/sidestore/01-pc-system-type.png（Windows の「バージョン情報」にある「システムの種類」）

**こうなれば OK**　「64 ビット オペレーティング システム、x64 ベース プロセッサ」と表示されています。

**0-2. iTunes を入れます**

1. [Apple の iTunes のページ](https://www.apple.com/itunes/) から、Windows 版をダウンロードします（**Apple 公式サイト版をおすすめします**。SideStore の公式も、Apple から直接ダウンロードする版をすすめています）。
2. ダウンロードしたファイルを開いて、案内どおりに進めます。
3. 求められたら、PC を再起動します。

**こうなれば OK**　 スタートメニューに「iTunes」が出る（iTunes 自体は、開かなくてもかまいません）。

**0-3. iloader を入れます**

1. iloader の公式ページ（[iloader.app](https://iloader.app/)、または [GitHub のリリースページ](https://github.com/nab138/iloader/releases/latest)）を開きます。**これ以外のサイトからはダウンロードしないでください**（iloader の作者も、公式の入手先をこの 2 つだけと案内しています）。
2. Windows 用の **MSI** をダウンロードします（`iloader-windows-x64.msi`。SideStore の公式は、MSI をすすめています）。
3. ダウンロードしたファイルを開いて、案内どおりにインストールします。

**こうなれば OK**　 スタートメニューに「iloader」が出る。

<details>
<summary>つまずいたら（手順 0）</summary>

- **iTunes が Microsoft Store 版しか入らない・うまく動かない**: Apple 公式サイト版に入れ替えるか、Microsoft Store の **「Apple Devices」アプリ** を入れて試してください（SideStore の公式も、この方法を案内しています）。
- **インストールで管理者の確認（青い画面）が出る**: 「はい」を押します。

</details>

---

## 手順 1: iPhone を PC につなぐ

1. USB ケーブルで、iPhone と PC をつなぎます。
2. iPhone のロックを解除します（画面をつけて、Face ID かパスコードで開きます）。

**こうなれば OK**　 PhoneCam のウィザードの「iPhoneをUSBで接続」に、**「iPhoneが見つかりました」** と出て、自動で次の画面に進む。（ウィザードを使わないときは、次の手順の iloader に iPhone が出ればOKです。）

<details>
<summary>つまずいたら（手順 1）</summary>

- **「Apple USBドライバが見つかりません」と出る**: iTunes が入っていないか、PC の再起動がまだです。手順 0-2 をやって、PC を再起動します。
- **「iPhoneを待っています」のまま**: ケーブルを挿し直します。別の USB ポート（PC の背面など）や、別のケーブルも試します。iPhone のロックを解除したままにします。
- 詳しくは [[よくある質問とトラブル]] の「接続できない」へ。

</details>

---

## 手順 2: 「このコンピュータを信頼」を押す

1. iPhone に **「このコンピュータを信頼しますか？」** と出たら、**信頼** を押します。
2. パスコードを求められたら、iPhone のパスコードを入力します。

> 📷 画像: images/sidestore/02-trust-prompt.png（iPhone の「このコンピュータを信頼しますか？」の画面）

**こうなれば OK**　 ウィザードに **「信頼済みです」** と出る（ウィザードを使わないときは、次の手順の iloader に iPhone の名前が出る）。

> 「信頼」の確認は、PC とつなぐたびに出ることがあります。そのたびに押してください。

<details>
<summary>つまずいたら（手順 2）</summary>

- **確認が出ない**: iPhone のロックを解除してから、ケーブルを挿し直します。
- **以前に「信頼しない」を押してしまった**: iPhone の **設定 > 一般 > 転送または iPhone をリセット > リセット > 位置情報とプライバシーをリセット** をすると、もう一度出ます（iPhone のデータは消えませんが、位置情報などの許可が、すべてもとに戻ります）。

</details>

---

## 手順 3: iloader で SideStore を入れる

ここが PC を使う、いちばん大事な手順です。iPhone は **USB でつないだまま**にします。

1. PC で **iloader** を開きます。
2. **Apple ID** のメールアドレスとパスワードを入力して、サインインします（**大文字と小文字は区別されます**）。

> 📷 画像: images/sidestore/03-iloader-login.png（iloader の Apple ID サインイン画面。メールアドレスは隠す）

3. 確認コードを求められたら、Apple から届いたコードを入力します。

**こうなれば OK**　 iloader のメイン画面に、Apple ID が表示される。

4. 画面で、**つないだ iPhone** を選びます。

> 📷 画像: images/sidestore/04-iloader-main.png（iloader のメイン画面。iPhone を選んだ状態と、「Install SideStore (Stable)」のボタン。Apple ID・iPhone の名前は隠す）

5. **Install SideStore (Stable)** を押します。
6. 終わるまで待ちます（数分かかります）。**iPhone と USB を外さないでください**。

> 📷 画像: images/sidestore/05-iloader-done.png（iloader の完了表示）

**こうなれば OK**　 iPhone のホーム画面に、**SideStore** のアイコンが出る。

> **Stable（安定版）** を選んでください。Nightly（最新の開発版）は、不安定なことがあります。

> **Apple ID は、iPhone にサインインしているものと同じでなくてもかまいません。** サブの Apple ID でも大丈夫です。ただし、**あとの手順 6 でも、ここと同じ Apple ID を使います**。

<details>
<summary>つまずいたら（手順 3）</summary>

- **iloader に iPhone が出ない**: 手順 1・2 を、もう一度確認します。PhoneCam のウィザードの **「SideStoreの導入チェック」** ボタンを押すと、iTunes（Apple Mobile Device Service）・iPhone の接続・信頼を、まとめて確認できます。
- **「Verification codes can't be sent to this phone number」と出て、サインインできない**: Apple の SMS 送信回数制限が考えられます。**時間をおいて**から、もう一度試します。急ぐときは、その Apple ID でサインインしている **ほかの端末（iPad・Mac など）に出る確認コード** を使います。何度も連続で押さないでください。
- **「Apple ID またはパスワードが違います」と出る**: 大文字と小文字、全角・半角を確かめます。Apple のサイト（appleid.apple.com）で、同じ組み合わせでサインインできるか試すのも、確実な方法です。
- **ほかのエラーが出る**: iloader のエラー画面は、原因の候補を出してくれます。[[よくある質問とトラブル]] の「サインインできない・エラーが出る」も見てください。
- **iloader の質問先**: iloader の不具合は、SideStore ではなく、iloader の作者（[nab138/iloader](https://github.com/nab138/iloader)）や、idevice の Discord に聞く決まりです。
- **iloader のログ**（質問のときに役立ちます）: `%APPDATA%\me.nabdev.iloader\logs`

</details>

---

## 手順 4: デベロッパ App を信頼して、デベロッパモードをオンにする

iPhone だけで行います。ここからは PC は使いません（USB は、つないだままで大丈夫です）。

**4-1. デベロッパ App を信頼する**

1. iPhone の **設定** を開きます。
2. **一般** を押します。
3. **VPNとデバイス管理**（英語表示では VPN & Device Management）を押します。
4. 「デベロッパApp」（Developer App）の下にある、**自分の Apple ID** を押します。

> 📷 画像: images/sidestore/06-vpn-device-management.png（「VPNとデバイス管理」の画面。「デベロッパApp」の下に Apple ID が出ている。Apple ID は隠す）

5. **「（Apple ID）を信頼」**（Trust）を押します。
6. 確認が出たら、**許可**（Allow）を押します。iPhone が再起動することがあります。
7. パスコードを入力します。

**こうなれば OK**　 もう一度「VPNとデバイス管理」を開くと、自分の Apple ID が信頼済みになっている。

**4-2. デベロッパモードをオンにする**

1. **設定** > **プライバシーとセキュリティ**（Privacy & Security）を開きます。
2. いちばん下までスクロールして、**デベロッパモード**（Developer Mode）を押します。
3. スイッチを **オン** にします。
4. 再起動を求められたら、**再起動** を押します。

> 📷 画像: images/sidestore/07-developer-mode.png（プライバシーとセキュリティの下のほうにある「デベロッパモード」のスイッチ）

5. iPhone が再起動したら、ロックを解除します。
6. デベロッパモードをオンにするか確認が出たら、**オンにする** を押して、パスコードを入力します。

> 📷 画像: images/sidestore/08-developer-mode-confirm.png（再起動後に出る、デベロッパモードをオンにする確認画面）

**こうなれば OK**　 「デベロッパモード」の項目が **オン** になっている。

> iPhone が 2 回再起動することがあります。慌てずに、そのたびにロックを解除します。

<details>
<summary>つまずいたら（手順 4）</summary>

- **「デベロッパモード」が出ない**: 手順 3 の **Install SideStore (Stable)** が終わっていないか、iPhone をまだ再起動していません。iPhone を再起動して、もう一度「プライバシーとセキュリティ」のいちばん下を見ます。詳しくは [[よくある質問とトラブル]] の「『デベロッパモード』が出ない」へ。
- **「VPNとデバイス管理」に自分の Apple ID が出ない**: 手順 3 の SideStore のインストールが終わっていません。iloader の画面に戻って、確認します。
- **SideStore を開くと「信頼されていないデベロッパ」と出る**: 4-1 の「信頼」を押していません。

</details>

---

## 手順 5: LocalDevVPN を入れて、Connect する

**LocalDevVPN**（ローカルデブ ブイピーエヌ）は、SideStore が iPhone の中で通信するために使う、無料の VPN アプリです。**外のサーバーにはつながりません**（iPhone の中だけの通信です）。SideStore でアプリを入れたり更新したりするときに、毎回オンにします。

1. iPhone の **App Store** で、**LocalDevVPN** を検索して、入れます（[App Store のページ](https://apps.apple.com/app/localdevvpn/id6755608044)）。PhoneCam のウィザードの QR コードを iPhone のカメラで読み取ると、そのページが開きます。

> 📷 画像: images/sidestore/09-appstore-localdevvpn.png（App Store の LocalDevVPN のページ）

2. **LocalDevVPN** を開きます。
3. **Connect** を押します。
4. 「VPN 構成の追加」を求められたら、**許可**（Allow）を押します。
5. パスコードを求められたら入力します。

> 📷 画像: images/sidestore/10-localdevvpn-allow.png（「VPN構成の追加を許可」の確認画面）

**こうなれば OK**　 LocalDevVPN の画面が接続中になり、iPhone の画面上部に **VPN のマーク** が出る。

> 📷 画像: images/sidestore/11-localdevvpn-connected.png（LocalDevVPN が Connect 済みの画面と、画面上部の VPN マーク）

> 以降、SideStore で何かをするときは、**いつも LocalDevVPN を Connect にして、Wi-Fi につないでおきます**。

<details>
<summary>つまずいたら（手順 5）</summary>

- **VPN を使っている別のアプリ（WireGuard など）がある**: オフにします。VPN は、同時に 1 つしか使えません。
- **広告ブロックなどの DNS のアプリがある**: オフにします。
- **Connect を押しても、すぐに切れる**: LocalDevVPN を開き直して、もう一度押します。iPhone を再起動すると直ることもあります。
- **あとで「VPN に接続してください」と出る**: Wi-Fi と VPN の接続を確認したうえで、LocalDevVPN の **Tunnel IP** を `10.7.0.0/32` に変更して再接続します。[[よくある質問とトラブル]] の「LocalDevVPN につながっているのに『VPN に接続してください』と出る」へ。

</details>

---

## 手順 6: SideStore にサインインして、最初の更新をする

SideStore が使えるようにするための手順です。**この「最初の更新」を必ずやってから、PhoneCam を入れてください。**

1. iPhone で **LocalDevVPN** が Connect になっていて、**Wi-Fi** につながっていることを確かめます。
2. **SideStore** を開きます。
3. 手順 3 の iloader と **同じ Apple ID** で、サインインします。

> 📷 画像: images/sidestore/12-sidestore-signin.png（SideStore のサインイン画面。メールアドレスは隠す）

4. 確認コードを求められたら、入力します。
5. 画面の下の **My Apps** を開きます。
6. **SideStore** の右にある **7 DAYS**（あと 7 日で期限が切れる、という意味）を押します。

> 📷 画像: images/sidestore/13-sidestore-myapps.png（SideStore の My Apps。SideStore の右に「7 DAYS」が出ている）

7. 「署名証明書を失効させるか、新しく作るか」という確認が出たら、**Yes**（または **Refresh Now**）を押します。

> 📷 画像: images/sidestore/14-sidestore-refresh-confirm.png（証明書の確認ダイアログ）

8. 待ちます。

**こうなれば OK**　 iPhone が **ホーム画面に戻り**、通知が出る。数秒後に、SideStore がまた使えるようになる。ここまでで、SideStore の準備は完了です。**USB ケーブルを外して大丈夫です。**

<details>
<summary>つまずいたら（手順 6）</summary>

- **「Verification codes can't be sent to this phone number」と出る**: 手順 3 のつまずきと同じです。Apple の SMS の回数制限なので、時間をおくか、ほかの端末に出るコードを使います。
- **サインインできない**: メールアドレスとパスワード（大文字と小文字）を確かめます。**Settings** で、**Anisette URL**（サインインを手伝うサーバー）を変えると、直ることがあります（サーバーが一時的に止まっていることがあります）。
- **「VPN に接続してください」「No Wi-Fi or VPN Connection」「AFC Connection Failure」と出る**: LocalDevVPN が Connect か、Wi-Fi につながっているかを確かめて、もう一度押します。それでもだめなら [[よくある質問とトラブル]] の「更新できない」へ。
- **SideStore が開かない・途中で止まる**: [[よくある質問とトラブル]] の「SideStore が開かない・途中で止まる」へ。
- **エラーに番号が出た**: [SideStore 公式のエラーコード一覧](https://docs.sidestore.io/docs/troubleshooting/error-codes) で調べられます。

</details>

---

## 手順 7: PhoneCam を入れる

PhoneCam は、SideStore の **ソース**（アプリの配布元のリスト）として配っています。ソースを追加すると、SideStore から PhoneCam を入れられて、新しい版が出たときも更新できます。

**方法 A: QR コードで追加する（ウィザードを使っているとき・かんたん）**

1. PC の PhoneCam のウィザードで、「SideStoreにPhoneCamを追加して入れる」の画面を開きます。
2. iPhone の **カメラ** で、画面の **QR コード** を読み取ります。
3. SideStore で開く表示を押します。
4. ソースの確認画面で **追加** を押します。

**こうなれば OK**　SideStore の **Sources** に PhoneCam の配布元が表示されます。

**方法 B: URL を貼り付けて追加する（QR が使えないとき）**

1. 次の URL を、iPhone にコピーします（メモアプリやメッセージで、自分に送ると楽です）。

```
https://github.com/syuuga101/phonecam/releases/latest/download/sidestore-source.json
```

2. **SideStore** を開きます。
3. 下のタブの **Sources** を開きます。
4. **＋** ボタンを押します。
5. URL を貼り付けます。
6. **追加** を押します。

> 📷 画像: images/sidestore/15-sidestore-sources-add.png（SideStore の Sources で「＋」を押して、URL を入れる画面）

**こうなれば OK**　**Sources** に PhoneCam の配布元が表示されます。

**どちらの方法でも、そのあとは同じです。**

1. **Browse** を開きます。
2. **PhoneCam** の **GET**（入手）を押します。

> 📷 画像: images/sidestore/16-sidestore-phonecam-get.png（PhoneCam のソースのページ。「GET」ボタン）

3. インストールが終わるまで待ちます。

**こうなれば OK**　 iPhone のホーム画面に **PhoneCam** のアイコンが出る。

<details>
<summary>つまずいたら（手順 7）</summary>

- **入れるときにエラーが出る**: LocalDevVPN が Connect か、Wi-Fi につながっているかを確かめます。
- **「3 アプリまで」と出る**: 無料の Apple ID では、同時に 3 つまでです（SideStore を含む）。使っていないアプリを、SideStore の **My Apps** から消します。[[よくある質問とトラブル]] の「『3 アプリまで』と出る」へ。
- **「requires additional permissions not specified by the source」と出る**: PhoneCam v1.3.5 以降で直っています。ソースを入れ直して、最新版を入手します。
- **ソースが使えない・PhoneCam が一覧に出ない**: PhoneCam の `.ipa` ファイルを直接入れる方法があります。iPhone の Safari などで、[Releases](https://github.com/syuuga101/phonecam/releases/latest) の `PhoneCam.ipa` を保存して、SideStore の **My Apps** の **＋** から、保存したファイルを選びます。この方法だと、新しい版は **自分で入れ直します**。PC のウィザードの「PhoneCam.ipaをダウンロード」ボタンでも、ファイルを手に入れられます。

</details>

---

## 手順 8: 最初の起動と接続テスト

**8-1. iPhone で PhoneCam を開く**

1. iPhone で **PhoneCam** を開きます。
2. **カメラ** と **ローカルネットワーク** の許可を求められたら、**許可** を押します（マイクと写真は、使う機能を初めて操作したときに、求められます）。

> 📷 画像: images/sidestore/17-phonecam-permissions.png（カメラの許可を求める画面）

**こうなれば OK**　 iPhone の画面に、カメラの映像が出る。

**8-2. PC の受信アプリとつなぐ**

1. iPhone を USB で PC につなぎます。
2. iPhone で PhoneCam を**前面に出しておきます**。
3. PC で、PhoneCam の受信アプリを開きます。ウィザードを進めている場合は「接続テスト」の画面を表示します。

> 📷 画像: images/sidestore/18-wizard-test-done.png（PC のウィザードの「接続テスト」で、「PhoneCamアプリとつながりました」と出ている画面）

**こうなれば OK**　 ウィザードに **「PhoneCamアプリとつながりました」** と出る（受信アプリのほうなら、映像が流れる）。これで、**iPhone へのアプリの導入は完了**です。お疲れさまでした。

このあとは、[[OBSで使う]] や [[Discordで使う]] へ。

<details>
<summary>つまずいたら（手順 8）</summary>

- **PhoneCam を開くと、すぐに閉じる・開かない**: 手順 4 でデベロッパモードを確認します。期限切れの場合は [[アップデート方法]] の復旧手順を使います。
- **「iPhoneのPhoneCamアプリに接続できません」と出る**: iPhone で PhoneCam を起動して、前面に出したままにします。
- **「信頼されていないデベロッパ」と出る**: 手順 4-1 をやります。
- 接続の問題は、[[よくある質問とトラブル]] の「接続できない」へ。

</details>

---

## 無料の Apple ID の制限

無料の Apple ID で、PhoneCam を使うときの制限です。覚えておいてください。

| 制限 | 内容 | どうすればいい？ |
|---|---|---|
| **アプリは同時に 3 つまで** | SideStore 自身も 1 つに数えます。PhoneCam を入れると、残りは 1 つです。 | 使わないアプリを、SideStore の My Apps から消します。 |
| **7 日で期限が切れる** | 期限が切れると、アプリが開かなくなります。 | **7 日ごとに、SideStore で更新します**（[[アップデート方法]] の「7 日ごとの更新」）。 |
| **7 日間に使える App ID は 10 個まで** | App ID は署名時に使う識別子です。アプリの拡張機能も枠を使うことがあります。 | SideStore の App ID 一覧で期限を確認し、枠が空くのを待ちます。 |

このガイドの手順で、無料の Apple ID と SideStore 単体を使って PhoneCam の導入・更新ができます。[SideStore 公式 FAQ](https://docs.sidestore.io/docs/faq)

---

## 7 日ごとの更新（かんたんな説明）

SideStore は、期限が近づくと、iPhone の中で自動で更新しようとします。ただし、確実ではないので、**週に 1 回、SideStore を開いて「7 DAYS」の数字を確かめる**のがおすすめです。手動で更新するときは、LocalDevVPN を Connect にして、Wi-Fi につないで、My Apps の **7 DAYS** を押します。

くわしい手順と、**更新を忘れて期限が切れたときの直し方**は、[[アップデート方法]] へ。

---

## AltStore から乗り換える

これまで **AltStore**（と PC の **AltServer**）で PhoneCam を入れていた人は、SideStore に乗り換えると、**PC がなくても iPhone だけで更新**できて、楽になります。次の 3 つをします。

**1. SideStore を入れる**

上の 手順 0〜6 のとおりに、SideStore を入れます。AltStore は、そのままで構いません（途中で消す必要はありません）。

> 無料の Apple ID で同時に入れられるのは **3 アプリまで**です。すでに AltStore や PhoneCam などで 3 枠を使っていると、SideStore を追加できません。そのときは、先に AltStore を消して（下の「3.」）から、SideStore を入れてください。

**2. PhoneCam を SideStore から入れ直す**

1. SideStore の **Sources** に、PhoneCam の配布元を追加します（上の 手順 7）。
2. PhoneCam を入れます。AltStore で入れた PhoneCam がある場合は、まず削除せずに SideStore からのインストールを試します。

   > 上書きされないで、2 つ並んだり、エラーになったりしたときは、AltStore 側の PhoneCam を一度消してから入れ直してください（PhoneCam の設定は iPhone の中のものなので、消すと戻ります）。

3. 入れた PhoneCam を開いて、PC の受信アプリとつながることを確かめます。

**3. AltStore と AltServer を消してよい**

SideStore から PhoneCam が動くことを確かめたら、次を消して構いません。

- **iPhone**: ホーム画面の **AltStore** を長押しして、アプリを削除します。
- **PC**: AltServer をアンインストールします（設定 > アプリ から）。iTunes は、PhoneCam が USB でつなぐのに使うので、**残してください**。iCloud は、AltServer のためだけに入れたのなら、消して構いません。

---

## 上級者向け（任意）　LiveContainer を使う

通常の導入は、SideStore 単体で PhoneCam を入れて更新する手順 0〜8 で完了します。

**LiveContainer** は、iPhone 上でほかのアプリを内部で実行するアプリです。SideStore を内蔵した版もあります。導入する場合は [LiveContainer + SideStore の公式手順](https://livecontainer.github.io/docs/installation/lc_sidestore) を参照してください。

PhoneCam の接続テストは、SideStore から iPhone に直接インストールした状態で行います。

<details>
<summary>LiveContainer + SideStore で error 1006 が出るとき</summary>

UDID は iPhone を識別する ID です。SideStore がこの ID を取得できないときは、[[よくある質問とトラブル]] の「LiveContainer + SideStore で error 1006 が出る」に沿って、iloader でペアリングファイルを作り直します。

</details>

---

## うまくいかないとき

- [[よくある質問とトラブル]]: 症状から探せます（VPN のエラー、更新できない、サインインできない、ペアリングファイルなど）。
- [[アップデート方法]]: 7 日ごとの更新と、期限が切れたときの直し方。
- [SideStore 公式の導入手順](https://docs.sidestore.io/docs/installation/install) / [iloader 公式](https://github.com/nab138/iloader) / [LocalDevVPN 公式](https://github.com/jkcoxson/LocalDevVPN)
- [SideStore 公式のトラブルシューティング](https://docs.sidestore.io/docs/troubleshooting/common-issues) / [FAQ](https://docs.sidestore.io/docs/faq)
- 解決しないときは、[Issues](https://github.com/syuuga101/phonecam/issues) へ。

---

## 用語集

| 用語 | 意味 |
|---|---|
| サイドロード | App Store 以外の方法で、アプリを入れること。 |
| 署名 | 「この Apple ID の人が入れたアプリです」という印をつけること。無料の Apple ID の署名は 7 日で切れます。 |
| SideStore | 署名とアプリの更新を、iPhone の中だけでしてくれる、無料のアプリ。PhoneCam を入れる「入れ物」です。 |
| iloader | SideStore を iPhone に入れる、PC 用のソフト。最初の 1 回と、ペアリングファイルの入れ直しで使います。 |
| LocalDevVPN | SideStore が iPhone の中で通信するための VPN アプリ。外のサーバーにはつながりません。 |
| ソース | アプリの配布元のリスト。PhoneCam のソースを SideStore に追加すると、PhoneCam を入れたり更新したりできます。 |
| デベロッパモード | 自分で入れたアプリを動かすための、iPhone の設定。オンにしないと、サイドロードしたアプリが動きません。 |
| ペアリングファイル | iPhone と PC が「お互いを知っている」という印のファイル。SideStore が iPhone と通信するのに使います。 |
| Apple ID | Apple のアカウント（現在は「Apple アカウント」とも呼ばれます）。 |
| UDID | iPhone 1 台ごとの識別番号。エラーの表示に出ることがあります（人に見せないでください）。 |
| LiveContainer | アプリの中でアプリを動かすアプリ。上級者向けです。 |
