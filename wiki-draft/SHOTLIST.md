# SideStore 撮影リスト

記事にある画像メモと同じファイル名で撮影します。共有用 PNG を書き出す前に、下表の項目を隠します。Apple ID のパスワードや確認コード、ペアリングファイルの中身は画面に表示せずに撮影します。画面名やボタンは読める大きさで残してください。

| ファイル名 | どの記事のどこ | PC / iPhone | 何の画面を写すか | 隠すべき個人情報 |
| --- | --- | --- | --- | --- |
| `images/sidestore/01-pc-system-type.png` | iPhoneアプリの入れ方（SideStore）／手順 0: PC に iTunes と iloader を入れる | PC | Windows の「バージョン情報」にある「システムの種類」 | PC 名・デバイス ID・プロダクト ID |
| `images/sidestore/02-trust-prompt.png` | iPhoneアプリの入れ方（SideStore）／手順 2: 「このコンピュータを信頼」を押す | iPhone | iPhone の「このコンピュータを信頼しますか？」の画面 | 背後の通知・端末名 |
| `images/sidestore/03-iloader-login.png` | iPhoneアプリの入れ方（SideStore）／手順 3: iloader で SideStore を入れる | PC | iloader の Apple ID サインイン画面。メールアドレスは隠す | Apple ID・パスワード・電話番号・確認コード |
| `images/sidestore/04-iloader-main.png` | iPhoneアプリの入れ方（SideStore）／手順 3: iloader で SideStore を入れる | PC | iloader のメイン画面。iPhone を選んだ状態と、「Install SideStore (Stable)」のボタン。Apple ID・iPhone の名前は隠す | Apple ID・端末名・UDID・シリアル番号 |
| `images/sidestore/05-iloader-done.png` | iPhoneアプリの入れ方（SideStore）／手順 3: iloader で SideStore を入れる | PC | iloader の完了表示 | Apple ID・端末名・UDID・ログ内のユーザー名 |
| `images/sidestore/06-vpn-device-management.png` | iPhoneアプリの入れ方（SideStore）／手順 4: デベロッパ App を信頼して、デベロッパモードをオンにする | iPhone | 「VPNとデバイス管理」の画面。「デベロッパApp」の下に Apple ID が出ている。Apple ID は隠す | Apple ID・プロファイル名 |
| `images/sidestore/07-developer-mode.png` | iPhoneアプリの入れ方（SideStore）／手順 4: デベロッパ App を信頼して、デベロッパモードをオンにする | iPhone | プライバシーとセキュリティの下のほうにある「デベロッパモード」のスイッチ | 通知・アカウント名 |
| `images/sidestore/08-developer-mode-confirm.png` | iPhoneアプリの入れ方（SideStore）／手順 4: デベロッパ App を信頼して、デベロッパモードをオンにする | iPhone | 再起動後に出る、デベロッパモードをオンにする確認画面 | 通知・パスコード（入力画面は撮らない） |
| `images/sidestore/09-appstore-localdevvpn.png` | iPhoneアプリの入れ方（SideStore）／手順 5: LocalDevVPN を入れて、Connect する | iPhone | App Store の LocalDevVPN のページ | Apple ID・プロフィール画像 |
| `images/sidestore/10-localdevvpn-allow.png` | iPhoneアプリの入れ方（SideStore）／手順 5: LocalDevVPN を入れて、Connect する | iPhone | 「VPN構成の追加を許可」の確認画面 | 通知・ほかの VPN 名 |
| `images/sidestore/11-localdevvpn-connected.png` | iPhoneアプリの入れ方（SideStore）／手順 5: LocalDevVPN を入れて、Connect する<br>アップデート方法／用意するもの | iPhone | LocalDevVPN が Connect 済みの画面と、画面上部の VPN マーク | SSID・通知・個人の IP アドレス |
| `images/sidestore/12-sidestore-signin.png` | iPhoneアプリの入れ方（SideStore）／手順 6: SideStore にサインインして、最初の更新をする | iPhone | SideStore のサインイン画面。メールアドレスは隠す | Apple ID・パスワード・電話番号・確認コード |
| `images/sidestore/13-sidestore-myapps.png` | iPhoneアプリの入れ方（SideStore）／手順 6: SideStore にサインインして、最初の更新をする | iPhone | SideStore の My Apps。SideStore の右に「7 DAYS」が出ている | Apple ID・他の個人用アプリ |
| `images/sidestore/14-sidestore-refresh-confirm.png` | iPhoneアプリの入れ方（SideStore）／手順 6: SideStore にサインインして、最初の更新をする | iPhone | 証明書の確認ダイアログ | Apple ID・証明書の識別情報 |
| `images/sidestore/15-sidestore-sources-add.png` | iPhoneアプリの入れ方（SideStore）／手順 7: PhoneCam を入れる | iPhone | SideStore の Sources で「＋」を押して、URL を入れる画面 | クリップボードの個人情報・非公開 URL |
| `images/sidestore/16-sidestore-phonecam-get.png` | iPhoneアプリの入れ方（SideStore）／手順 7: PhoneCam を入れる | iPhone | PhoneCam のソースのページ。「GET」ボタン | Apple ID・ほかの配布元やアプリ |
| `images/sidestore/17-phonecam-permissions.png` | iPhoneアプリの入れ方（SideStore）／手順 8: 最初の起動と接続テスト | iPhone | カメラの許可を求める画面 | 背景の人物・住所・通知 |
| `images/sidestore/18-wizard-test-done.png` | iPhoneアプリの入れ方（SideStore）／手順 8: 最初の起動と接続テスト | PC | PC のウィザードの「接続テスト」で、「PhoneCamアプリとつながりました」と出ている画面 | 端末名・IP アドレス・ペアリングコード・映像内の個人情報 |
| `images/sidestore/19-sidestore-update.png` | アップデート方法／PhoneCam を新しいバージョンにする | iPhone | PhoneCam の更新ボタン | Apple ID・他の個人用アプリ |
| `images/sidestore/20-refresh-both.png` | アップデート方法／7 日ごとの更新（期限を延ばす） | iPhone | My Apps の SideStore と PhoneCam の残り日数 | Apple ID・他の個人用アプリ |
| `images/sidestore/21-tunnel-ip.png` | よくある質問とトラブル／LocalDevVPN につながっているのに「VPN に接続してください」と出る | iPhone | LocalDevVPN の設定で Tunnel IP が 10.7.0.0/32 になっている画面 | SSID・通知・個人の IP アドレス（指定の Tunnel IP は表示） |
| `images/sidestore/22-iloader-pairing.png` | よくある質問とトラブル／ペアリングファイルの期限切れ | PC | iloader の Manage Pairing File と配置の成功表示 | Apple ID・端末名・UDID・シリアル番号・ファイル名と内容 |
