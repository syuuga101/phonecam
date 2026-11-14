# 開発者向けガイド

PhoneCam を自分でビルドしたい人、コードを変更したい人向けの資料です。使うだけの方は [README](../README.md) か [使い方ガイド](https://github.com/syuuga101/phonecam/wiki) を見てください。

関連資料: [ARCHITECTURE.md](ARCHITECTURE.md)（しくみ） / [PROTOCOL.md](PROTOCOL.md)（通信プロトコル） / [CONTRIBUTING.md](../CONTRIBUTING.md)

## リポジトリの構成

```
phonecam/
├─ VERSION                      バージョン番号（これを変えて main に push するとリリース）
├─ CHANGELOG.md                 変更履歴（リリースノートの元）
├─ ios/                         iPhone アプリ（Swift / XcodeGen）
│   ├─ project.yml
│   └─ PhoneCam/*.swift         CameraController, H264Encoder, StreamServer, Recorder, Effects, LowLatencyMic, ...
├─ windows/                     Windows 受信アプリ（Python / PySide6）
│   ├─ phonecam_qt.py           入口（配布 exe の入口。start.bat・管理者ヘルパの --register / --setup もここ）
│   ├─ phonecam/                本体（core/ = 受信・デコード・仮想カメラなど、qt/ = 画面、controller.py = 画面と core をつなぐ層、labels.py = 表示ラベル）
│   ├─ phonecam_iphone.py       iPhone との接続まわり（usbmux など）
│   ├─ phonecam_audio.py        音声の再生
│   ├─ phonecam_setup.py        セットアップウィザード
│   ├─ phonecam_qr.py           QR コード生成
│   ├─ softcam_ext/             専用カメラ（softcam）の改造パッチとビルドスクリプト
│   ├─ install.bat / start.bat  ソース実行用
│   └─ register.bat / unregister.bat  専用カメラの登録／解除（ソース実行用）
├─ .github/
│   ├─ workflows/ios.yml        iOS ビルド（署名なし IPA）
│   ├─ workflows/windows.yml    softcam のビルド + exe のビルド
│   ├─ workflows/release.yml    リリース作成
│   └─ scripts/                 make_altstore_source.py（AltStore ソース JSON の生成）、list_bundle.py（exe の同梱物の一覧）
└─ docs/                        ドキュメント
```

## ビルドのしかた

どちらも GitHub Actions で自動ビルドします。Mac は不要です。

### iPhone アプリ（IPA）

1. リポジトリを fork（または自分のリポジトリへ push）します。
2. GitHub の **Actions** タブで「Build iOS (unsigned IPA)」を選び、**Run workflow** を押します（`ios/` を変更して push しても自動で動きます）。
3. 数分で終わります。実行結果の **Artifacts** から `PhoneCam-ipa` をダウンロードすると `PhoneCam.ipa` が入っています。

ワークフローの流れ: XcodeGen で Xcode プロジェクトを作成 → 署名なしでビルド（`MARKETING_VERSION` は `VERSION` ファイル、`CURRENT_PROJECT_VERSION` は実行番号）→ アドホック署名とエンタイトルメント（`get-task-allow`）を付けて IPA にまとめる。署名なしのままだと AltStore / AltServer が読み込めないことがあるため仮署名を付けています。実際の署名は AltStore 側でやり直されます。

> AltStore のソース（`altstore-source.json`）の `appPermissions` にも `get-task-allow` を書く必要があります。書いていないと AltStore が「requires additional permissions not specified by the source」で拒否します。

### Windows 受信アプリ（exe）

「Build Windows receiver」ワークフローが次を行い、`PhoneCamReceiver.exe` を作ります。

1. Python をセットアップして依存ライブラリ（`windows/requirements.txt`）をインストール
2. [softcam](https://github.com/tshino/softcam) をクローンし、表示名・CLSID・共有メモリ名を PhoneCam 用に書き換え（他の softcam 利用アプリと衝突しないようにするため）
3. MSBuild で `softcam.dll` をビルド（x64。Win32 は任意）
4. PyInstaller（`--onefile --windowed`、入口は `phonecam_qt.py`）で、受信アプリと `softcam.dll`（x64 / あれば Win32）を `PhoneCamReceiver.exe` 1 つにまとめる（バージョンとリポジトリ名は `phonecam_version.py` として生成して埋め込む）

> 専用カメラの CLSID は `windows/phonecam/core/softcam_reg.py` の `PHONECAM_CLSID` と `windows/softcam_ext/build-slots.ps1` で必ず一致させてください。

### ソースから実行（Windows）

Python 3.12 が必要です（「Add python.exe to PATH」と「py launcher」にチェック）。

1. `windows` フォルダの **install.bat** を実行（初回のみ。av / numpy / pyvirtualcam と、音声用の sounddevice が入ります）
2. iPhone で PhoneCam を起動して画面を表示したままにします
3. **start.bat** で受信アプリが開きます（中身は `py -3 phonecam_qt.py`）
4. 「USB」または「Wi-Fi」を選んで接続します

専用カメラを使いたいときは、自分でビルドした `softcam.dll` を `windows` フォルダに置いて `register.bat` を実行します（手順は `windows.yml` を参照）。無ければ OBS Virtual Camera を使います。`sounddevice` が入っていないと音声タブは使えません（映像は使えます）。

設定は `%APPDATA%\PhoneCam\phonecam_settings.json` に保存されます。

## Windows 受信アプリのテスト

リポジトリのルートで、Python 3.12 の環境から実行します。

```powershell
python -m pip install -r windows/requirements-test.txt
python -m pytest windows/tests -ra
```

Qt のテストは既定で `QT_QPA_PLATFORM=offscreen` を使います。iPhone、仮想カメラの登録、ネットワーク接続は不要です。
Qt の見た目を確認するときは既存の `python windows/qt_smoke.py --out qt-screens` を使います。
GitHub Actions では Windows の pytest ジョブに失敗すると exe ビルドがスキップされ、リリースも公開されません。

## リリースの手順

リリースは `VERSION` ファイルの変更で始まります。

1. `CHANGELOG.md` の先頭に `## v<新バージョン>` の項目を書きます（ユーザー向けの言葉で、「新機能」「改善」「修正」など）。
2. `VERSION` を新しい番号に書き換えます（例: `1.4.0`）。
3. 同じコミットで `main` に push します。
4. `release.yml` が iOS と Windows のビルドを呼び出し、成功したら次を行います。
   - `CHANGELOG.md` から `## v<バージョン>` の項目だけを取り出し、固定のインストール案内とあわせてリリース本文にする（項目が無いときは既定の文章）。`generate_release_notes` による自動生成の一覧が続きます
   - `make_altstore_source.py` で `altstore-source.json` を作成
   - `PhoneCam.ipa` / `PhoneCamReceiver.exe` / `altstore-source.json` を添付してリリースを公開

`workflow_dispatch` で手動実行もできます。AltStore のソース URL（`https://github.com/syuuga101/phonecam/releases/latest/download/altstore-source.json`）は常に最新リリースを指します。

ワークフローで使うサードパーティの Action（`softprops/action-gh-release`）は、コミット SHA で固定しています。更新するときは、新しいタグの SHA を調べて書き換えてください。

## 動作確認のめやす

- iOS: 実機で、起動・カメラ許可・USB 接続・Wi-Fi 接続（許可して 6 桁コード）・録画
- Windows: exe でセットアップウィザード、OBS / Discord に「PhoneCam」が出て映像が流れる、切断→再接続
- プロトコルを変えるときは、古い iPhone アプリ・古い PC アプリとも動くこと（未知の種別・キーは無視する）を確認してください。詳しくは [PROTOCOL.md](PROTOCOL.md)。
