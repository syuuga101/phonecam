# -*- coding: utf-8 -*-
"""PhoneCam セットアップ

必要なソフト (Apple USBドライバ / iCloud / AltServer / VB-CABLE / VC++ ランタイム / PhoneCam カメラ) の
検出・ダウンロード・インストールと、初回起動のセットアップウィザードを提供します。

受信アプリ本体 (phonecam_receiver.py) とは bind() で関数を受け渡して連携します
(本体を __main__ として実行しても二重 import にならないようにするため)。
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import queue
import re
import webbrowser
import urllib.error
import urllib.request

try:
    from phonecam_version import REPO as APP_REPO  # ビルド時に生成される ("owner/name")
except Exception:
    APP_REPO = ""

try:
    from phonecam_version import VERSION as APP_VERSION
except Exception:
    APP_VERSION = "dev"

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None

try:
    import phonecam_iphone as iphone  # iPhone のセットアップ案内 (usbmux への問い合わせ・手順の判定)
except Exception:  # pragma: no cover
    iphone = None

try:
    import phonecam_qr  # 依存なしの QR コード生成
except Exception:  # pragma: no cover
    phonecam_qr = None


# ---------------------------------------------------------------------------
# 多言語化 (phonecam.i18n が無い / キーが未登録のときは下の日本語がそのまま出る)
# ---------------------------------------------------------------------------

_JA = {
    "setup.wizard.list_sep": "、",
    "setup.wizard.sentence_fmt": "%s。",
    "setup.wizard.components_1": "PhoneCamカメラ",
    "setup.wizard.components_2": "OBSやDiscordに表示される専用カメラ",
    "setup.wizard.components_3": "Visual C++ ランタイム",
    "setup.wizard.components_4": "専用カメラの動作に必要",
    "setup.wizard.components_5": "Apple USBドライバ（iTunes）",
    "setup.wizard.components_6": "USBケーブルでの接続に必要（Wi-Fiだけなら不要）",
    "setup.wizard.components_7": "AltServerの動作に必要",
    "setup.wizard.components_8": "iPhoneにアプリを入れて自動更新する",
    "setup.wizard.components_9": "iPhoneのマイクをOBSで使う（任意）。今のスピーカーとマイクは既定のまま変わりません",
    "setup.wizard.manual_text_1": "公式サイトから「vc_redist.x64.exe」をダウンロードして実行してください。",
    "setup.wizard.manual_text_2": "公式サイトの「Windows版iTunes（Microsoft Store版ではない方）」をダウンロードして実行してください。",
    "setup.wizard.manual_text_3": "AltStoreの案内ページにある「iCloud（Apple公式サイト版）」のリンクからインストールしてください。",
    "setup.wizard.manual_text_4": "公式サイトから「AltServer for Windows」をダウンロードし、zipを展開して Setup.exe を実行してください。",
    "setup.wizard.manual_text_5": "公式サイトから zip をダウンロードして展開し、VBCABLE_Setup_x64.exe を管理者として実行してください。",
    "setup.wizard.detect_camera_1": "ソース実行ではregister.batを使ってください",
    "setup.wizard.detect_camera_2": "更新が必要です",
    "setup.wizard.detect_camera_3": "このexeにはカメラが含まれていません",
    "setup.wizard.detect_itunes_1": "・",
    "setup.wizard.detect_itunes_2": "Microsoft Store版の%sが入っています。AltServerと一緒に使えないため、インストール時に削除して公式サイト版のiTunesに入れ替えます",
    "setup.wizard.detect_itunes_3": "USB接続はOK・AltServerには公式サイト版が必要",
    "setup.wizard.store_labels_1": "Appleデバイス",
    "setup.wizard.remove_store_package_1": "Microsoft Store版を削除できませんでした。設定 > アプリ から手動でアンインストールしてください（詳細: %s）",
    "setup.wizard.remove_store_package_2": "Microsoft Store版を削除できませんでした。設定 > アプリ から手動でアンインストールしてください（詳細: コード %s）",
    "setup.wizard.remove_store_package_3": "Microsoft Store版（%s）を削除しました",
    "setup.wizard.detect_icloud_1": "Microsoft Store版が入っています（インストール時に削除して公式サイト版に入れ替えます）",
    "setup.wizard.detect_all_1": "確認できませんでした。「再チェック」を押してください",
    "setup.wizard.friendly_net_error_1": "ダウンロード元が応答しませんでした。しばらくしてからもう一度試すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.friendly_net_error_2": "ダウンロードがタイムアウトしました。ネットワークを確認して、もう一度お試しください",
    "setup.wizard.friendly_net_error_3": "インターネットに接続できませんでした。ネットワークを確認して、もう一度お試しください",
    "setup.wizard.friendly_net_error_4": "ダウンロードに失敗しました。ネットワークを確認して、もう一度お試しください",
    "setup.wizard.validate_download_1": "ダウンロードしたファイルが見つかりません。もう一度お試しください",
    "setup.wizard.validate_download_2": "ダウンロードしたファイルが空でした。もう一度お試しください",
    "setup.wizard.validate_download_3": "ダウンロードしたファイルが小さすぎます（エラーページの可能性があります）。しばらくしてからもう一度試すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.validate_download_4": "ダウンロードが途中で切れました。ネットワークを確認して、もう一度お試しください",
    "setup.wizard.validate_download_5": "ダウンロードしたファイルの形式が正しくありません。「公式ページを開く」から手動で入れてください",
    "setup.wizard.validate_download_6": "zipファイルが壊れています。もう一度ダウンロードしてください",
    "setup.wizard.download_file_1": "安全でないURL (https以外) からはダウンロードしません。「公式ページを開く」から手動で入れてください",
    "setup.wizard.download_file_2": "ダウンロード先が安全でないURL (https以外) に転送されたため中止しました。「公式ページを開く」から手動で入れてください",
    "setup.wizard.download_file_3": "キャンセルされました。もう一度やり直すときは「もう一度試す」を押してください",
    "setup.wizard.safe_extract_zip_1": "zipの中身が不正です。もう一度ダウンロードするか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.safe_extract_zip_2": "zipの展開後のサイズが大きすぎます。「公式ページを開く」から手動で入れてください",
    "setup.wizard.evaluate_signature_1": "このインストーラーは署名の確認対象として登録されていないため、実行しませんでした。「公式ページを開く」から手動で入れてください",
    "setup.wizard.evaluate_signature_2": "署名を確認しました（%s）",
    "setup.wizard.evaluate_signature_3": "不明",
    "setup.wizard.evaluate_signature_4": "インストーラーの署名者が想定と違うため、実行しませんでした（想定: %s / 実際: %s）。ダウンロードをやり直してください",
    "setup.wizard.evaluate_signature_5": "署名のないファイルです（%s は署名なしで配布されています）。ファイルの改ざん検知のみで実行します",
    "setup.wizard.evaluate_signature_6": "インストーラーに電子署名がないため、実行しませんでした（%s の署名が必要です）。ダウンロードをやり直すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.evaluate_signature_7": "インストーラーの電子署名が無効です（改ざんされているか、PCの時刻・証明書の更新が古い可能性があります）。実行しませんでした。ダウンロードをやり直すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.evaluate_signature_8": "インストーラーの署名を確認できなかったため、実行しませんでした。もう一度試すか、「公式ページを開く」から手動で入れてください（詳細: %s）",
    "setup.wizard.evaluate_signature_9": "不明なエラー",
    "setup.wizard.verify_authenticode_1": "Windows 以外では確認できません",
    "setup.wizard.step_register_camera_1": "PhoneCamカメラを登録",
    "setup.wizard.stage_for_run_1": "インストーラーの場所が不正です",
    "setup.wizard.explain_exit_code_1": "インストールが途中で取り消されました。もう一度やり直してください",
    "setup.wizard.explain_exit_code_2": "インストール中にエラーが起きました。PCを再起動してから、もう一度お試しください",
    "setup.wizard.explain_exit_code_3": "。Microsoft Store版が残っていると失敗します。設定 > アプリ からStore版をアンインストールしてください",
    "setup.wizard.explain_exit_code_4": "ほかのインストールが動いています。終わるまで待ってから、もう一度お試しください",
    "setup.wizard.explain_exit_code_5": "インストーラーのファイルが壊れています。ダウンロードからやり直してください",
    "setup.wizard.explain_exit_code_6": "権限が足りないか、PCの設定でブロックされました。PhoneCamReceiver.exe を右クリック →「管理者として実行」でやり直してください",
    "setup.wizard.explain_exit_code_7": "すでに新しい版が入っています。そのままお使いいただけます",
    "setup.wizard.explain_exit_code_8": "インストーラーが失敗しました。もう一度試して、だめなら「公式ページを開く」から手動で入れてください",
    "setup.wizard.run_installer_1": "インストーラーが変更されていたため実行しませんでした。もう一度インストールをやり直してください",
    "setup.wizard.run_installer_2": "終了コード %s",
    "setup.wizard.execute_step_1": "カメラの登録に失敗しました。OBSやDiscordなどを閉じてから、もう一度お試しください",
    "setup.wizard.execute_step_2": "インストーラーが見つかりません。もう一度ダウンロードからやり直してください",
    "setup.wizard.execute_step_3": "インストーラーの検証情報がないため実行しませんでした。もう一度ダウンロードからやり直してください",
    "setup.wizard.execute_step_4": "許可されていないwingetパッケージのため実行しませんでした。「公式ページを開く」から手動で入れてください",
    "setup.wizard.execute_step_5": "wingetでのインストールに失敗しました。「公式ページを開く」から手動で入れてください",
    "setup.wizard.execute_step_6": "winget 終了コード %s",
    "setup.wizard.execute_step_7": "不明な手順です。PhoneCamを最新版にしてからやり直してください",
    "setup.wizard.execute_step_8": "インストールが時間内に終わりませんでした。PCを再起動してから、もう一度お試しください",
    "setup.wizard.execute_step_9": "予期しないエラーが起きました。もう一度お試しください",
    "setup.wizard.run_job_1": "ジョブファイルの検証情報がないため実行しませんでした",
    "setup.wizard.run_job_2": "ジョブファイルが変更されています",
    "setup.wizard.run_job_3": "セットアップの準備でエラーが起きました。もう一度お試しください（詳細: %s）",
    "setup.wizard.slow_hint_1": "インストーラーの画面がほかのウィンドウの後ろに隠れていないか確認してください（タスクバーのアイコンも見てください）",
    "setup.wizard.missing_common_1": "インストーラーを最後の「完了」まで進めてから「確認」を押してください。「再起動が必要」と出たときは、PCを再起動してから、セットアップタブの「再チェック」を押してください。",
    "setup.wizard.guide_1": "「インストーラーを開く」を押します（Appleのインストーラーが開きます）",
    "setup.wizard.guide_2": "「次へ」→ 使用許諾に同意 →「インストール」を押します",
    "setup.wizard.guide_3": "「ユーザーアカウント制御」が出たら「はい」を押します",
    "setup.wizard.guide_4": "「完了」が出たら押して、この画面の「確認」を押します",
    "setup.wizard.guide_5": "オプションはそのままで大丈夫です。再起動を求められたら「あとで」を選んでください。",
    "setup.wizard.guide_6": "Apple公式サイト版のiTunesがまだ見つかりません",
    "setup.wizard.guide_7": "「インストーラーを開く」を押します（iCloudのインストーラーが開きます）",
    "setup.wizard.guide_8": "使用許諾に同意して「インストール」を押します",
    "setup.wizard.guide_9": "完了したら「完了」を押して、この画面の「確認」を押します",
    "setup.wizard.guide_10": "iCloudにサインインする必要はありません。再起動を求められたら「いいえ（あとで）」を選び、すべて終わってからPCを再起動してください。",
    "setup.wizard.guide_11": "Apple公式サイト版のiCloudがまだ見つかりません",
    "setup.wizard.guide_12": "「インストーラーを開く」を押します（AltServerのインストーラーが開きます）",
    "setup.wizard.guide_13": "「Next」→「Install」を押します（Windowsの青い警告が出たら「詳細情報」→「実行」）",
    "setup.wizard.guide_14": "「Finish」を押します。画面右下のタスクトレイにAltServerのアイコンが出ます",
    "setup.wizard.guide_15": "この画面の「確認」を押します",
    "setup.wizard.guide_16": "Windowsファイアウォールの許可画面が出たら、「プライベート ネットワーク」にチェックを入れて「アクセスを許可する」を押してください（AltServerがiPhoneを見つけるのに必要です）。",
    "setup.wizard.guide_17": "AltServerがまだ見つかりません",
    "setup.wizard.guide_18": "「インストーラーを開く」を押します（VB-CABLEのセットアップが開きます）",
    "setup.wizard.guide_19": "「Install Driver」ボタンを押します",
    "setup.wizard.guide_20": "「このデバイスソフトウェアをインストールしますか?」が出たら「インストール」を押します",
    "setup.wizard.guide_21": "「OK」で閉じて、この画面の「確認」を押します",
    "setup.wizard.guide_22": "再起動が必要と出たら、すべて終わってからPCを再起動してください。インストール中に既定のスピーカーが切り替わっても、PhoneCamが元に戻します。",
    "setup.wizard.guide_23": "VB-CABLE（CABLE Input）がまだ見つかりません",
    "setup.wizard.missing_words_1": "「%s」はまだ使える状態になっていません（%s）",
    "setup.wizard.missing_words_2": "「%s」がまだ見つかりません",
    "setup.wizard.shell_open_1": "Windows 以外では開けません",
    "setup.wizard.shell_open_2": "ShellExecute に失敗しました (%s)",
    "setup.wizard.guidedflow_skip_1": "あとで入れます。セットアップタブの「インストール」からいつでも入れられます",
    "setup.wizard.guidedflow_prepare_1": "%s: すでに入っています",
    "setup.wizard.guidedflow_prepare_2": "すでに入っていました",
    "setup.wizard.guidedflow_prepare_3": "%s のMicrosoft Store版を削除しています…",
    "setup.wizard.prepare_prog_1": "%s をダウンロード中… %s / %s",
    "setup.wizard.prepare_prog_2": "%s をダウンロード中… %s",
    "setup.wizard.guidedflow_prepare_4": "%s をダウンロード中…",
    "setup.wizard.guidedflow_prepare_5": "%s: ダウンロード完了",
    "setup.wizard.guidedflow_prepare_6": "%s のインストーラーを確認しています…",
    "setup.wizard.guidedflow_prepare_7": "このインストーラーには電子署名がありません。Windowsの青い警告が出たら「詳細情報」→「実行」を押してください。",
    "setup.wizard.guidedflow_prepare_8": "インストーラーの準備に失敗しました。もう一度試すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.guidedflow_prepare_9": "%s: 「インストーラーを開く」を押してください",
    "setup.wizard.guidedflow__locate_1": "zipの中にインストーラーが見つかりませんでした。「公式ページを開く」から手動で入れてください",
    "setup.wizard.guidedflow_launch_1": "インストーラーが変更されていたため開きませんでした。「もう一度試す」でダウンロードからやり直してください",
    "setup.wizard.guidedflow_launch_2": "インストーラーのファイルが見つかりません。「もう一度試す」でダウンロードからやり直してください",
    "setup.wizard.guidedflow_launch_3": "管理者の確認で「いいえ」が押されました。もう一度「インストーラーを開く」を押して、「はい」を選んでください",
    "setup.wizard.guidedflow_launch_4": "インストーラーを開けませんでした。ウイルス対策ソフトがブロックしていないか確認して、「もう一度試す」を押すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.guidedflow_check_1": "インストーラーがまだ動いています。画面の案内に沿って「完了」まで進めてから、もう一度「確認」を押してください",
    "setup.wizard.run_diagnostics_1": "AltServer が起動しているか確認しています…",
    "setup.wizard.run_diagnostics_2": "AltServer が起動している",
    "setup.wizard.run_diagnostics_3": "確認できませんでした",
    "setup.wizard.run_diagnostics_4": "もう一度「診断」を押してください",
    "setup.wizard.run_diagnostics_5": "AltServer が動いていません",
    "setup.wizard.run_diagnostics_6": "スタートメニューから AltServer を起動してください（セットアップタブの「AltServerを起動」でも開けます）",
    "setup.wizard.run_diagnostics_7": "Apple Mobile Device Service が動いている",
    "setup.wizard.run_diagnostics_8": "Apple公式サイト版のiTunesを入れて、PCを再起動してください（セットアップタブの「インストール」から入れられます）",
    "setup.wizard.run_diagnostics_9": "サービスを開始してください（Windowsキー → 「サービス」→ Apple Mobile Device Service →「開始」）。直らないときはPCを再起動します",
    "setup.wizard.run_diagnostics_10": "Bonjour Service が動いている",
    "setup.wizard.run_diagnostics_11": "Bonjour は公式サイト版のiTunes / iCloudと一緒に入ります。公式サイト版を入れ直してください",
    "setup.wizard.run_diagnostics_12": "サービスを開始してください（Windowsキー → 「サービス」→ Bonjour サービス →「開始」）。直らないときはPCを再起動します",
    "setup.wizard.run_diagnostics_13": "%s を確認しています…",
    "setup.wizard.run_diagnostics_14": "サービスが止まっています",
    "setup.wizard.run_diagnostics_15": "サービスが見つかりません",
    "setup.wizard.run_diagnostics_16": "iTunes を確認しています…",
    "setup.wizard.run_diagnostics_17": "公式サイト版のiTunesが入っている",
    "setup.wizard.run_diagnostics_18": "Microsoft Store版は使えません。セットアップタブの「インストール」で公式サイト版に入れ替えてください",
    "setup.wizard.run_diagnostics_19": "iTunes が見つかりません",
    "setup.wizard.run_diagnostics_20": "セットアップタブの「インストール」からApple公式サイト版のiTunesを入れてください",
    "setup.wizard.run_diagnostics_21": "iPhone を確認しています…",
    "setup.wizard.run_diagnostics_22": "iPhoneがUSBでつながっている",
    "setup.wizard.run_diagnostics_23": "つながっていますが、このPCを信頼していません",
    "setup.wizard.run_diagnostics_24": "iPhoneのロックを解除して、「このコンピュータを信頼」→「信頼」を押してください",
    "setup.wizard.run_diagnostics_25": "iPhoneが見つかりません",
    "setup.wizard.run_diagnostics_26": "LightningケーブルでPCにつなぎ、iPhoneのロックを解除して「信頼」を押してください。更新中はUSBでつないだままにするのがいちばん確実です",
    "setup.wizard.run_diagnostics_27": "ネットワークの設定を確認しています…",
    "setup.wizard.run_diagnostics_28": "ネットワークの種類が「プライベート」",
    "setup.wizard.run_diagnostics_29": "設定 > ネットワークとインターネット > Wi-Fi（またはイーサネット）> 接続中のネットワーク で「プライベート」を選んでください",
    "setup.wizard.run_diagnostics_30": "VPN がオフ",
    "setup.wizard.run_diagnostics_31": "Tailscale などのVPNを使っているときは、いったんオフにしてください",
    "setup.wizard.run_diagnostics_32": "設定 > ネットワークとインターネット > Wi-Fi（またはイーサネット）> 接続中のネットワーク を開き、ネットワーク プロファイルを「プライベート」にしてください",
    "setup.wizard.run_diagnostics_33": "ネットワークが見つかりません",
    "setup.wizard.run_diagnostics_34": "Wi-Fi または有線LANにつないでください",
    "setup.wizard.run_diagnostics_35": "「パブリック」になっています（%s）",
    "setup.wizard.run_diagnostics_36": "「パブリック」のネットワークもあります（%s）",
    "setup.wizard.run_diagnostics_37": "iPhoneと同じネットワークが「パブリック」なら、",
    "setup.wizard.run_diagnostics_38": "VPNらしい接続が有効です（%s）",
    "setup.wizard.run_diagnostics_39": "Tailscale などのVPNをいったんオフにしてから更新してください（AltServerがiPhoneを見つけにくくなります）",
    "setup.wizard.run_diagnostics_40": "ファイアウォールを確認しています…（少し時間がかかります）",
    "setup.wizard.run_diagnostics_41": "ファイアウォールがAltServerを許可している",
    "setup.wizard.run_diagnostics_42": "AltServer を起動したときの Windows セキュリティの画面で、「プライベート ネットワーク」を許可してください",
    "setup.wizard.run_diagnostics_43": "AltServer をブロックするルールがあります",
    "setup.wizard.run_diagnostics_44": "Windowsセキュリティ > ファイアウォールとネットワーク保護 > ファイアウォールによるアプリケーションの許可 で、AltServer の「プライベート」にチェックを入れてください",
    "setup.wizard.run_diagnostics_45": "AltServer を許可するルールが見つかりません",
    "setup.wizard.run_diagnostics_46": "AltServer を起動し直して、出てきた画面で「プライベート ネットワーク」にチェックして「アクセスを許可する」を押してください。出ないときは、Windowsセキュリティ > ファイアウォールによるアプリケーションの許可 から AltServer を追加します",
    "setup.wizard.diagnostics_summary_1": "問題は見つかりませんでした。それでも更新できないときは、下の「エラー2003が出るとき」を上から順に試してください。",
    "setup.wizard.diagnostics_summary_2": "直す必要があるもの: %d 件",
    "setup.wizard.diagnostics_summary_3": "確認してほしいもの: %d 件",
    "setup.wizard.diagnostics_summary_4": "。下の「→」の案内を上から順に試してください。",
    "setup.wizard.setupengine__prepare_1": "%s をインストール",
    "setup.wizard.setupengine__prepare_2": "zipの中にインストーラーが見つかりませんでした",
    "setup.wizard.setupengine_run_1": "準備に失敗しました。もう一度お試しください",
    "setup.wizard.setupengine_run_2": "%s: wingetで代わりにインストールします",
    "setup.wizard.setupengine_run_3": "%s をインストール (winget)",
    "setup.wizard.setupengine_run_4": "結果を確認中…",
    "setup.wizard.setupengine_run_5": "インストール後の確認に失敗しました。「再チェック」を押してください（詳細: %s）",
    "setup.wizard.setupengine_run_6": "インストールは終わりましたが、まだ認識できません。再起動してから再チェックしてください",
    "setup.wizard.setupengine_run_7": "登録の処理は終わりましたが、状態が変わっていません。もう一度お試しください",
    "setup.wizard.setupengine__run_elevated_1": "管理者の確認が表示されたら「はい」を押してください…",
    "setup.wizard.setupengine__run_elevated_2": "管理者権限でインストールを開始します (%d 件)",
    "setup.wizard.setupengine__run_elevated_3": "管理者の確認がキャンセルされたため、インストールしませんでした。もう一度お試しください。",
    "setup.wizard.setupengine__run_elevated_4": "インストールが時間内に終わりませんでした。もう一度お試しください。",
    "setup.wizard.setupengine__run_elevated_5": "インストールを実行できませんでした。もう一度お試しください。だめなときは PhoneCamReceiver.exe を右クリック →「管理者として実行」でやり直してください",
    "setup.wizard.setupengine__run_elevated_6": "詳細: %s %s",
    "setup.wizard.setupengine__run_elevated_7": "完了",
    "setup.wizard.setupengine__run_elevated_8": "失敗 ",
    "setup.wizard.setupengine__run_elevated_9": "実行されませんでした",
    "setup.wizard.setupcontroller_start_1": "準備しています…",
    "setup.wizard.start_work_1": "セットアップ中にエラーが起きました。もう一度お試しください（詳細: %s）",
    "setup.wizard.guided_run_work_1": "予期しないエラーが起きました。「もう一度試す」を押すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.guided_open_work_1": "インストーラーを開けませんでした。「もう一度試す」を押すか、「公式ページを開く」から手動で入れてください",
    "setup.wizard.icons_1": "導入済み",
    "setup.wizard.icons_2": "未導入",
    "setup.wizard.icons_3": "要確認",
    "setup.wizard.setuppanel_render_1": "確認しています…",
    "setup.wizard.setuppanel__row_1": "失敗",
    "setup.wizard.setuppanel__row_2": "公式のインストーラーを開いて、手順を画面で案内します",
    "setup.wizard.setuppanel__wizard_action_1": "スキップ",
    "setup.wizard.setuppanel__tab_action_1": "更新",
    "setup.wizard.setuppanel__tab_action_2": "インストール",
    "setup.wizard.diagnosticsdialog___init___1": "AltServerの診断",
    "setup.wizard.diagnosticsdialog___init___2": "閉じる",
    "setup.wizard.diagnosticsdialog___init___3": "もう一度診断する",
    "setup.wizard.diagnosticsdialog_run_1": "診断しています…",
    "setup.wizard.run_work_1": "診断",
    "setup.wizard.run_work_2": "診断を実行できませんでした (%s)",
    "setup.wizard.run_work_3": "もう一度「診断する」を押してください",
    "setup.wizard.diagnosticsdialog_render_1": "更新に使うPCの条件",
    "setup.wizard.diagnosticsdialog_render_2": "AltStoreは、どのPCのAltServerからでも更新できます。ただし、そのPCで次がそろっている必要があります。",
    "setup.wizard.diagnosticsdialog_render_3": "AltStore公式の手順を開く",
    "setup.wizard.setupwizard_1": "ようこそ",
    "setup.wizard.setupwizard_2": "必要なソフトの確認",
    "setup.wizard.setupwizard_3": "iPhoneの準備",
    "setup.wizard.setupwizard___init___1": "PhoneCam セットアップ",
    "setup.wizard.setupwizard__page_welcome_1": "PhoneCam へようこそ",
    "setup.wizard.setupwizard__page_welcome_2": "iPhoneをOBSのカメラにするために必要なソフトを確認します。すでに入っているものは飛ばします。\nVisual C++ とカメラの登録は自動で入れます（管理者の確認が1回出ます）。\niTunes・iCloud・AltServer・VB-CABLE は、公式のインストーラーを画面に開いて、押すボタンを1つずつご案内します。\nそのあと、iPhoneにPhoneCamを入れる手順もご案内します。",
    "setup.wizard.setupwizard__page_welcome_3": "次へ",
    "setup.wizard.setupwizard__page_check_1": "iPhoneをマイクとしても使う",
    "setup.wizard.setupwizard__page_check_2": "OBSやDiscordでiPhoneの音声を使うための「VB-CABLE」（無料）を一緒に入れます。PCのマイクを使う場合は不要です。",
    "setup.wizard.setupwizard__page_check_3": "再チェック",
    "setup.wizard.setupwizard__page_install_1": "詳細 ▸",
    "setup.wizard.setupwizard__page_install_2": "もう一度試す",
    "setup.wizard.setupwizard__page_install_3": "あとで（中止）",
    "setup.wizard.setupwizard__toggle_log_1": "詳細 ▾",
    "setup.wizard.setupwizard_overall_text_1": "続けて、下の案内に沿ってインストールします",
    "setup.wizard.setupwizard_overall_text_2": "一部のインストールが完了しませんでした（下の案内を見てください）",
    "setup.wizard.setupwizard_overall_text_3": "完了しました（あとで入れる項目があります）",
    "setup.wizard.setupwizard_overall_text_4": "完了しました",
    "setup.wizard.setupwizard__show_outcome_1": "再起動が必要です。セットアップがすべて終わったあとに、PCを再起動してください。",
    "setup.wizard.setupwizard__show_outcome_2": "%s は自動で入れられませんでした。「%s」",
    "setup.wizard.setupwizard__show_outcome_3": "%s の公式ページを開く",
    "setup.wizard.setupwizard__render_results_1": "詳細: %s",
    "setup.wizard.setupwizard__render_guided_1": "%s をインストール（%d / %d）",
    "setup.wizard.setupwizard__render_guided_2": "インストーラーを準備しています。上のバーが進みます…",
    "setup.wizard.setupwizard__render_guided_3": "インストールされたか確認しています…",
    "setup.wizard.setupwizard__render_guided_4": "✓ インストールを確認できました。インストーラーの「完了」を押して閉じると、次に進みます（このまま「確認」を押しても進めます）",
    "setup.wizard.setupwizard__render_guided_5": "インストーラーを開く",
    "setup.wizard.setupwizard__render_guided_6": "確認",
    "setup.wizard.setupwizard__render_guided_7": "インストーラーをもう一度開く",
    "setup.wizard.setupwizard__render_guided_8": "公式ページを開く",
    "setup.wizard.setupwizard__render_guided_9": "この項目はあとで",
    "setup.wizard.setupwizard__elapsed_text_1": "インストーラーを実行中です（経過 %d:%02d）。画面の案内に沿って進めてください",
    "setup.wizard.setupwizard__page_step_1": "全%d段階",
    "setup.wizard.setupwizard__page_step_2": "あとで",
    "setup.wizard.setupwizard__page_step_3": "戻る",
    "setup.wizard.setupwizard__extra_itunes_1": "iTunesを開く",
    "setup.wizard.setupwizard__extra_itunes_2": "iTunesが見つかりません。前の画面でインストールできます。",
    "setup.wizard.setupwizard__extra_altstore_1": "AltServerを起動",
    "setup.wizard.setupwizard__extra_altstore_2": "公式の手順を開く",
    "setup.wizard.setupwizard__extra_altstore_3": "うまくいかないときは、AltServerを「管理者として実行」してみてください。",
    "setup.wizard.setupwizard__extra_phonecam_1": "iPhoneのカメラでQRコードを読み取り、AltStoreで開きます。",
    "setup.wizard.setupwizard__extra_phonecam_2": "追加できたら、AltStoreで「PhoneCam」を入手します。",
    "setup.wizard.setupwizard__extra_phonecam_3": "URLをコピー",
    "setup.wizard.setupwizard__extra_phonecam_4": "QRが使えないときは、PhoneCam.ipaを直接入れる方法もあります。AltServerのアイコンをShiftキーを押しながらクリック → 「Sideload .ipa…」→ 保存したPhoneCam.ipa を選びます。（この方法だと、アプリの更新は自分で入れ直します）",
    "setup.wizard.setupwizard__extra_phonecam_5": "インストール時に「AltServer.ServerError 2005」が出たときは、AltStoreの「My Apps」でAltStoreを一度「Refresh」してから、もう一度インストールしてください。",
    "setup.wizard.setupwizard__extra_phonecam_6": "PhoneCam.ipaをダウンロード",
    "setup.wizard.setupwizard__extra_phonecam_7": "保存しました: %s",
    "setup.wizard.setupwizard__copy_1": "✓ URLをコピーしました",
    "setup.wizard.setupwizard__download_ipa_1": "ダウンロード中…",
}


try:
    from phonecam.i18n import t as _i18n_t
except Exception:  # pragma: no cover
    _i18n_t = None


def t(key, **kw):
    text = None
    if _i18n_t is not None:
        try:
            text = _i18n_t(key)
        except Exception:
            text = None
    if text is None or text == key:
        text = _JA.get(key, key)
    if kw:
        try:
            text = text.format(**kw)
        except Exception:
            pass
    return text


# ---------------------------------------------------------------------------
# 本体との連携
# ---------------------------------------------------------------------------

_host = {}


def bind(**fns):
    """本体から関数を受け取る: is_frozen / registration_status / register_camera / run_elevated / script_path
    (任意: link_connected = 受信アプリが iPhone につながっているか)"""
    _host.update(fns)


def _call(name, *args, **kw):
    fn = _host.get(name)
    if fn is None:
        raise RuntimeError("setup host function not bound: %s" % name)
    return fn(*args, **kw)


def _frozen():
    fn = _host.get("is_frozen")
    return bool(fn()) if fn else bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATE_OK = "ok"            # 導入済み
STATE_MISSING = "missing"  # 未導入
STATE_WARN = "warn"        # 要確認

APPLE_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service"
USBMUX_ADDR = ("127.0.0.1", 27015)
VCREDIST_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
CABLE_KEYWORD = "CABLE Input"
FAQ_URL = "https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows"

# 追加の再起動コード
EXIT_REBOOT = (3010, 1641)
JOB_TIMEOUT_MS = 20 * 60 * 1000

# 表示順 (「必要なソフトの確認」の並び)
COMPONENTS = [
    {"id": "camera", "name": t("setup.wizard.components_1"), "purpose": t("setup.wizard.components_2"), "required": True},
    {"id": "vcredist", "name": t("setup.wizard.components_3"), "purpose": t("setup.wizard.components_4"), "required": True},
    {"id": "itunes", "name": t("setup.wizard.components_5"), "purpose": t("setup.wizard.components_6"), "required": True},
    {"id": "icloud", "name": "iCloud", "purpose": t("setup.wizard.components_7"), "required": False},
    {"id": "altserver", "name": "AltServer", "purpose": t("setup.wizard.components_8"), "required": False, "default_on": True},
    {"id": "vbcable", "name": "VB-CABLE", "purpose": t("setup.wizard.components_9"), "required": False},
]
COMPONENT_BY_ID = {c["id"]: c for c in COMPONENTS}
# インストールする順番
INSTALL_ORDER = ["vcredist", "itunes", "icloud", "altserver", "vbcable", "camera"]

SOURCES = {
    "vcredist": {
        "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe", "file": "vc_redist.x64.exe", "kind": "exe",
        "args": ["/install", "/quiet", "/norestart"], "ok_codes": [0, 1638, 3010, 1641],
        "winget": "Microsoft.VCRedist.2015+.x64",
        "page": "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist",
    },
    "itunes": {
        "url": "https://www.apple.com/itunes/download/win64", "file": "iTunes64Setup.exe", "kind": "exe",
        "args": ["/quiet", "/norestart"], "ok_codes": [0, 3010, 1641],
        "winget": "Apple.iTunes",
        "page": "https://www.apple.com/itunes/",
    },
    "icloud": {
        "url": "https://updates.cdn-apple.com/2020/windows/001-39935-20200911-1A70AA56-F448-11EA-8CC0-99D41950005E/iCloudSetup.exe",
        "file": "iCloudSetup.exe", "kind": "exe",
        "args": ["/quiet", "/norestart"], "ok_codes": [0, 3010, 1641],
        "page": FAQ_URL,
    },
    "altserver": {
        "url": "https://cdn.altstore.io/file/altstore/altinstaller.zip", "file": "altinstaller.zip", "kind": "zip",
        "page": "https://altstore.io/",
    },
    "vbcable": {
        "url": "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip", "file": "VBCABLE_Driver_Pack46.zip",
        "kind": "zip", "page": "https://vb-audio.com/Cable/",
    },
}

MANUAL_TEXT = {
    "vcredist": t("setup.wizard.manual_text_1"),
    "itunes": t("setup.wizard.manual_text_2"),
    "icloud": t("setup.wizard.manual_text_3"),
    "altserver": t("setup.wizard.manual_text_4"),
    "vbcable": t("setup.wizard.manual_text_5"),
}

DEFAULT_MIN_BYTES = 100 * 1024
MAX_UNZIPPED_BYTES = 1024 * 1024 * 1024   # zip 爆弾対策 (展開後の合計)
SIGNATURES = {"exe": (b"MZ",), "zip": (b"PK\x03\x04",), "msi": (b"\xd0\xcf\x11\xe0",)}
_CREATE_NO_WINDOW = 0x08000000


def setup_dir():
    return os.path.join(tempfile.gettempdir(), "PhoneCamSetup")


def ipa_url(repo=None):
    repo = APP_REPO if repo is None else repo
    if not repo or "/" not in repo:
        return None
    return "https://github.com/%s/releases/latest/download/PhoneCam.ipa" % repo


# ---------------------------------------------------------------------------
# 検出
# ---------------------------------------------------------------------------

def _winreg():
    try:
        import winreg
        return winreg
    except Exception:
        return None


def reg_key_exists(path, view=64):
    wr = _winreg()
    if wr is None:
        return False
    flag = wr.KEY_WOW64_64KEY if view == 64 else wr.KEY_WOW64_32KEY
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, path, 0, wr.KEY_READ | flag):
            return True
    except Exception:
        return False


def reg_value(path, name, view=64):
    wr = _winreg()
    if wr is None:
        return None
    flag = wr.KEY_WOW64_64KEY if view == 64 else wr.KEY_WOW64_32KEY
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, path, 0, wr.KEY_READ | flag) as k:
            return wr.QueryValueEx(k, name)[0]
    except Exception:
        return None


def uninstall_entries():
    """アンインストール情報 (HKLM の 64bit / 32bit 両方) から (表示名, バージョン) を列挙する。"""
    wr = _winreg()
    out = []
    if wr is None:
        return out
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for flag in (wr.KEY_WOW64_64KEY, wr.KEY_WOW64_32KEY):
        try:
            with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, base, 0, wr.KEY_READ | flag) as root:
                i = 0
                while True:
                    try:
                        sub = wr.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with wr.OpenKey(root, sub) as k:
                            try:
                                name = wr.QueryValueEx(k, "DisplayName")[0]
                            except OSError:
                                continue
                            try:
                                ver = wr.QueryValueEx(k, "DisplayVersion")[0]
                            except OSError:
                                ver = ""
                            out.append((str(name), str(ver)))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def tcp_open(addr=USBMUX_ADDR, timeout=0.4):
    try:
        with socket.create_connection(addr, timeout=timeout):
            return True
    except Exception:
        return False


def _program_dirs():
    out = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(key)
        if v and v not in out:
            out.append(v)
    return out or [r"C:\Program Files"]


def _any_file(rel_paths):
    for base in _program_dirs():
        for rel in rel_paths:
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p
    return None


def _has_entry(entries, *needles, exact=False):
    for name, _ in entries:
        low = name.strip().lower()
        for n in needles:
            if (low == n) if exact else (n in low):
                return True
    return False


def find_altserver():
    return _any_file([os.path.join("AltServer", "AltServer.exe")])


def audio_device_names():
    try:
        import sounddevice as sd
        return [str(d.get("name", "")) for d in sd.query_devices()]
    except Exception:
        return []


def detect_camera():
    if not _frozen():
        return {"state": STATE_WARN, "detail": t("setup.wizard.detect_camera_1")}
    st = _call("registration_status")
    if st == "ok":
        return {"state": STATE_OK, "detail": ""}
    if st == "outdated":
        return {"state": STATE_MISSING, "detail": t("setup.wizard.detect_camera_2")}
    if st == "unavailable":
        return {"state": STATE_WARN, "detail": t("setup.wizard.detect_camera_3")}
    return {"state": STATE_MISSING, "detail": ""}


def detect_vcredist():
    if reg_value(VCREDIST_KEY, "Installed") == 1:
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_itunes(entries=None):
    entries = uninstall_entries() if entries is None else entries
    service = reg_key_exists(APPLE_SERVICE_KEY) or tcp_open()
    nonstore = _has_entry(entries, "itunes", exact=True) or bool(_any_file([os.path.join("iTunes", "iTunes.exe")]))
    store = installed_store_packages("itunes")
    if store and (not nonstore or "AppleInc.AppleDevices" in store):
        names = t("setup.wizard.detect_itunes_1").join("「%s」" % STORE_LABELS.get(p, p) for p in store)
        return {"state": STATE_WARN,
                "detail": t("setup.wizard.detect_itunes_2") % names}
    if service and nonstore:
        return {"state": STATE_OK, "detail": ""}
    if service:
        return {"state": STATE_WARN, "detail": t("setup.wizard.detect_itunes_3")}
    return {"state": STATE_MISSING, "detail": ""}


# Microsoft Store 版のパッケージ名。公式サイト版と共存できず、AltServer は公式サイト版が必要なので入れ替える
# itunes には「Appleデバイス」アプリも含める。入っていると iTunes でiPhoneを扱えず、AltServer がiPhoneを見つけられない (エラー2003)
STORE_PACKAGES = {"icloud": ["AppleInc.iCloud"], "itunes": ["AppleInc.iTunes", "AppleInc.AppleDevices"]}
STORE_LABELS = {"AppleInc.iCloud": "iCloud", "AppleInc.iTunes": "iTunes", "AppleInc.AppleDevices": t("setup.wizard.store_labels_1")}


APPX_REPO_KEY = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"


def store_package_installed(cid):
    r"""Microsoft Store 版 (AppleInc.iCloud_* など) が今のユーザーに入っているか。
    %LOCALAPPDATA%\Packages のフォルダはアンインストール後も残ることがあるので使わず、
    インストール済みパッケージの登録 (HKCU の AppModel\Repository\Packages) を見る。"""
    return bool(installed_store_packages(cid))


def installed_store_packages(cid):
    """cid に関係する Store パッケージのうち、今のユーザーに入っているものの名前一覧"""
    wanted = STORE_PACKAGES.get(cid) or []
    wr = _winreg()
    if not wanted or wr is None:
        return []
    found = []
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, APPX_REPO_KEY) as root:
            i = 0
            while True:
                try:
                    name = wr.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                for pkg in wanted:
                    if name.startswith(pkg + "_") and pkg not in found:
                        found.append(pkg)
    except OSError:
        return []
    return found


def _store_icloud_installed():
    return store_package_installed("icloud")


def remove_store_package(cid, runner=None):
    """Store 版を現在のユーザーから削除する (管理者権限は不要。通常権限のプロセスで呼ぶこと)。
    戻り値: (成功したか, メッセージ)"""
    pkgs = STORE_PACKAGES.get(cid) or []
    if not pkgs:
        return True, ""
    script = "; ".join(
        "$p = Get-AppxPackage -Name '%s'; if ($p) { $p | Remove-AppxPackage -ErrorAction Stop }" % pkg for pkg in pkgs)
    script += "; if (%s) { exit 2 } else { exit 0 }" % " -or ".join("(Get-AppxPackage -Name '%s')" % pkg for pkg in pkgs)
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        if runner is not None:
            code = runner(cmd)
        else:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            code = subprocess.run(cmd, creationflags=flags, timeout=300,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except Exception as e:
        return False, t("setup.wizard.remove_store_package_1") % e
    if code != 0:
        return False, t("setup.wizard.remove_store_package_2") % code
    return True, t("setup.wizard.remove_store_package_3") % t("setup.wizard.detect_itunes_1").join(STORE_LABELS.get(p, p) for p in pkgs)


def detect_icloud(entries=None):
    entries = uninstall_entries() if entries is None else entries
    # 公式サイト版はアンインストール情報に「iCloud」で登録される。失敗したインストールの残りファイルで
    # 誤判定しないよう、ファイルの有無ではなく登録で判定する
    if _has_entry(entries, "icloud", exact=True):
        return {"state": STATE_OK, "detail": ""}
    if _store_icloud_installed():
        # Store 版があると公式サイト版のインストールは失敗する (終了コード 1603)。AltStore が動いていればそのままでよい
        return {"state": STATE_WARN, "detail": t("setup.wizard.detect_icloud_1")}
    return {"state": STATE_MISSING, "detail": ""}


def detect_altserver(entries=None):
    entries = uninstall_entries() if entries is None else entries
    if _has_entry(entries, "altserver") or find_altserver():
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_vbcable(entries=None, devices=None):
    entries = uninstall_entries() if entries is None else entries
    if _has_entry(entries, "vb-audio virtual cable", "vb-cable"):
        return {"state": STATE_OK, "detail": ""}
    names = audio_device_names() if devices is None else devices
    if any(CABLE_KEYWORD.lower() in n.lower() for n in names):
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_all():
    """全コンポーネントの状態: {id: {"state", "detail"}}。個別の失敗は「要確認」にして続ける。"""
    try:
        entries = uninstall_entries()
    except Exception:
        entries = []
    table = {
        "camera": detect_camera,
        "vcredist": detect_vcredist,
        "itunes": lambda: detect_itunes(entries),
        "icloud": lambda: detect_icloud(entries),
        "altserver": lambda: detect_altserver(entries),
        "vbcable": lambda: detect_vbcable(entries),
    }
    out = {}
    for cid, fn in table.items():
        try:
            out[cid] = fn()
        except Exception as e:
            out[cid] = {"state": STATE_WARN, "detail": t("setup.wizard.detect_all_1")}
    return out


# ---------------------------------------------------------------------------
# ダウンロード
# ---------------------------------------------------------------------------

class DownloadError(Exception):
    """利用者に見せる日本語メッセージ (次にやることを含む)。technical は「詳細」に出す技術的な内容。"""

    def __init__(self, message="", technical=""):
        Exception.__init__(self, message)
        self.technical = technical


def friendly_net_error(e):
    """ダウンロード失敗の説明 (番号は含めない。番号は technical_net_error で「詳細」へ)。"""
    if isinstance(e, urllib.error.HTTPError):
        return t("setup.wizard.friendly_net_error_1")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return t("setup.wizard.friendly_net_error_2")
    if isinstance(e, urllib.error.URLError):
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            return t("setup.wizard.friendly_net_error_2")
        return t("setup.wizard.friendly_net_error_3")
    return t("setup.wizard.friendly_net_error_4")


def technical_net_error(e):
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %s %s" % (e.code, getattr(e, "reason", ""))
    return "%s: %s" % (type(e).__name__, e)


def validate_download(path, kind, min_bytes=DEFAULT_MIN_BYTES, expected_size=None):
    """ダウンロードしたファイルが空でなく、種類 (exe/zip/msi) の先頭シグネチャと一致するか確認する。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        raise DownloadError(t("setup.wizard.validate_download_1"))
    if size <= 0:
        raise DownloadError(t("setup.wizard.validate_download_2"))
    if size < min_bytes:
        raise DownloadError(t("setup.wizard.validate_download_3"))
    if expected_size and size != expected_size:
        raise DownloadError(t("setup.wizard.validate_download_4"))
    with open(path, "rb") as f:
        head = f.read(8)
    sigs = SIGNATURES.get(kind)
    if sigs and not any(head.startswith(s) for s in sigs):
        raise DownloadError(t("setup.wizard.validate_download_5"))
    if kind == "zip" and not zipfile.is_zipfile(path):
        raise DownloadError(t("setup.wizard.validate_download_6"))
    return size


def download_file(url, dest, kind, progress=None, cancel=None, opener=None, timeout=30, min_bytes=DEFAULT_MIN_BYTES):
    """url を dest へ保存して検証する。progress(done, total) を随時呼ぶ (total 不明なら 0)。"""
    opener = opener or urllib.request.urlopen
    if not str(url).lower().startswith("https://"):
        raise DownloadError(t("setup.wizard.download_file_1"))
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneCamSetup/%s" % APP_VERSION})
    expected = 0
    try:
        with opener(req, timeout=timeout) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
            if isinstance(final_url, str) and not final_url.lower().startswith("https://"):
                raise DownloadError(t("setup.wizard.download_file_2"))
            try:
                expected = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                expected = 0
            done = 0
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadError(t("setup.wizard.download_file_3"))
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, expected)
    except DownloadError:
        _silent_remove(part)
        raise
    except Exception as e:
        _silent_remove(part)
        raise DownloadError(friendly_net_error(e), technical=technical_net_error(e))
    try:
        validate_download(part, kind, min_bytes=min_bytes, expected_size=expected or None)
    except DownloadError:
        _silent_remove(part)
        raise
    os.replace(part, dest)
    return dest


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def safe_extract_zip(zip_path, dest):
    """zip を dest へ展開する (dest の外へ出るパスは拒否)。"""
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    root = os.path.abspath(dest)
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            target = os.path.abspath(os.path.join(root, info.filename))
            # dest の外へ出るパス (../ や絶対パス・ドライブ指定) は拒否
            if (target != root and not target.startswith(root + os.sep)) or name.startswith("/") \
                    or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
                raise DownloadError(t("setup.wizard.safe_extract_zip_1"))
            total += info.file_size
        if total > MAX_UNZIPPED_BYTES:
            raise DownloadError(t("setup.wizard.safe_extract_zip_2"))
        z.extractall(root)
    return root


def find_file(root, names):
    """root 以下から、ファイル名 (小文字) が names のいずれかに一致する最初のファイルを返す。"""
    wanted = [n.lower() for n in names]
    hits = {}
    for d, _, files in os.walk(root):
        for f in files:
            low = f.lower()
            if low in wanted and low not in hits:
                hits[low] = os.path.join(d, f)
    for n in wanted:
        if n in hits:
            return hits[n]
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# インストーラーの署名確認 (Authenticode)
# ---------------------------------------------------------------------------
# 管理者権限で実行する前に、ダウンロードしたインストーラーの電子署名が有効で、
# 署名者が想定どおりの発行元かを確認する。署名者の判定ロジック (evaluate_signature) は
# 実際の確認処理 (verify_authenticode) と切り離してあり、確認処理を差し替えてテストできる。

# コンポーネントごとの想定する発行元。match: "exact" = 証明書の名前 (CN または O) と完全一致 (大文字小文字は無視) /
# "contains" = 部分一致。unsigned_ok: 署名のないファイルを許すか (許さない既定)
SIGNER_POLICY = {
    "vcredist": {"publisher": "Microsoft Corporation", "names": ["Microsoft Corporation"], "match": "exact", "unsigned_ok": False},
    "itunes": {"publisher": "Apple Inc.", "names": ["Apple Inc."], "match": "exact", "unsigned_ok": False},
    "icloud": {"publisher": "Apple Inc.", "names": ["Apple Inc."], "match": "exact", "unsigned_ok": False},
    "vbcable": {"publisher": "VB-Audio Software (Vincent Burel)", "names": ["VB-Audio", "Vincent Burel"], "match": "contains", "unsigned_ok": False},
    # AltServer は公式の配布物が署名されているか確認できていない。署名されていれば発行元を確認し、
    # 署名が無いときだけは「ジョブファイルのSHA-256による改ざん検知」(従来の保護) に頼って実行する。
    "altserver": {"publisher": "Riley Testut / AltStore", "names": ["Riley", "Testut", "AltStore"], "match": "contains",
                  "unsigned_ok": True},
}

# WinVerifyTrust の戻り値 (HRESULT)
TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
_UNSIGNED_CODES = (TRUST_E_NOSIGNATURE, TRUST_E_SUBJECT_FORM_UNKNOWN, TRUST_E_PROVIDER_UNKNOWN, 0x80070002, 0x80070003)


def classify_trust_result(ret, last_error):
    """WinVerifyTrust の結果 → "valid" / "unsigned" (署名なし) / "invalid" (署名はあるが無効・改ざん・失効など)。"""
    if (ret & 0xFFFFFFFF) == 0:
        return "valid"
    if (last_error & 0xFFFFFFFF) in _UNSIGNED_CODES or (ret & 0xFFFFFFFF) in _UNSIGNED_CODES:
        return "unsigned"
    return "invalid"


def _signer_matches(policy, signers):
    names = [n.strip().casefold() for n in signers if isinstance(n, str) and n.strip()]
    for want in policy["names"]:
        w = want.casefold()
        for n in names:
            if policy["match"] == "exact":
                if n == w:
                    return True
            # 部分一致は語順を問わない (例: 実際の署名者は "BUREL VINCENT Entrepreneur individuel")
            elif w in n or all(tok in n.split() for tok in w.split()):
                return True
    return False


def evaluate_signature(cid, info):
    """署名確認の結果 info = {"result": "valid"/"unsigned"/"invalid"/"error", "signers": [名前...], "code": int}
    から実行してよいかを決める。戻り値: (許可するか, 日本語メッセージ, 署名なしで許可したか)"""
    policy = SIGNER_POLICY.get(cid)
    if policy is None:
        return False, t("setup.wizard.evaluate_signature_1"), False
    result = (info or {}).get("result")
    if result == "valid":
        if _signer_matches(policy, (info or {}).get("signers") or []):
            return True, t("setup.wizard.evaluate_signature_2") % policy["publisher"], False
        found = ", ".join(n for n in (info.get("signers") or []) if n) or t("setup.wizard.evaluate_signature_3")
        return False, (t("setup.wizard.evaluate_signature_4") % (policy["publisher"], found)), False
    if result == "unsigned":
        if policy["unsigned_ok"]:
            return True, t("setup.wizard.evaluate_signature_5") % policy["publisher"], True
        return False, (t("setup.wizard.evaluate_signature_6") % policy["publisher"]), False
    if result == "invalid":
        return False, (t("setup.wizard.evaluate_signature_7")), False
    return False, (t("setup.wizard.evaluate_signature_8") % ((info or {}).get("detail") or t("setup.wizard.evaluate_signature_9"))), False


def _wintrust():
    """ctypes で WinVerifyTrust 周りの定義を作る (Windows のみ)。"""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pcwszFilePath", ctypes.c_wchar_p),
                    ("hFile", ctypes.c_void_p), ("pgKnownSubject", ctypes.c_void_p)]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", ctypes.c_uint32),
                    ("fdwRevocationChecks", ctypes.c_uint32), ("dwUnionChoice", ctypes.c_uint32),
                    ("pFile", ctypes.c_void_p), ("dwStateAction", ctypes.c_uint32),
                    ("hWVTStateData", ctypes.c_void_p), ("pwszURLReference", ctypes.c_wchar_p),
                    ("dwProvFlags", ctypes.c_uint32), ("dwUIContext", ctypes.c_uint32),
                    ("pSignatureSettings", ctypes.c_void_p)]

    class CRYPT_PROVIDER_CERT(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pCert", ctypes.c_void_p)]

    return ctypes, wintypes, GUID, WINTRUST_FILE_INFO, WINTRUST_DATA, CRYPT_PROVIDER_CERT


def verify_authenticode(path):
    """WinVerifyTrust (WINTRUST_ACTION_GENERIC_VERIFY_V2 / 失効確認なし / UI なし) で署名を検証し、署名者名を読む。
    戻り値: {"result": "valid"/"unsigned"/"invalid"/"error", "code": 戻り値, "signers": [CN, O], "detail": str}。
    Windows 以外・例外のときは "error" (呼び出し側は実行しない)。"""
    if os.name != "nt":
        return {"result": "error", "code": 0, "signers": [], "detail": t("setup.wizard.verify_authenticode_1")}
    try:
        ctypes, wintypes, GUID, FILE_INFO, WT_DATA, PROV_CERT = _wintrust()
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        wintrust.WinVerifyTrust.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_void_p]
        wintrust.WinVerifyTrust.restype = ctypes.c_int32
        wintrust.WTHelperProvDataFromStateData.argtypes = [ctypes.c_void_p]
        wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wintrust.WTHelperGetProvCertFromChain.restype = ctypes.POINTER(PROV_CERT)
        crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                               ctypes.c_wchar_p, ctypes.c_uint32]
        crypt32.CertGetNameStringW.restype = ctypes.c_uint32

        action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        fi = FILE_INFO(ctypes.sizeof(FILE_INFO), os.path.abspath(path), None, None)
        data = WT_DATA()
        data.cbStruct = ctypes.sizeof(WT_DATA)
        data.dwUIChoice = 2            # WTD_UI_NONE
        data.fdwRevocationChecks = 0   # WTD_REVOKE_NONE
        data.dwUnionChoice = 1         # WTD_CHOICE_FILE
        data.pFile = ctypes.cast(ctypes.pointer(fi), ctypes.c_void_p)
        data.dwStateAction = 1         # WTD_STATEACTION_VERIFY (署名者を読むため、状態を残す)
        INVALID = ctypes.c_void_p(-1)  # INVALID_HANDLE_VALUE (デスクトップのウィンドウを使わない)
        ctypes.set_last_error(0)
        ret = wintrust.WinVerifyTrust(INVALID, ctypes.byref(action), ctypes.byref(data))
        last = ctypes.get_last_error()
        signers = []
        try:
            if data.hWVTStateData:
                prov = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
                sgnr = wintrust.WTHelperGetProvSignerFromChain(prov, 0, 0, 0) if prov else None
                cert = wintrust.WTHelperGetProvCertFromChain(sgnr, 0) if sgnr else None
                ctx = cert.contents.pCert if cert else None
                if ctx:
                    for kind, para in ((4, None), (3, ctypes.cast(ctypes.c_char_p(b"2.5.4.10"), ctypes.c_void_p))):
                        buf = ctypes.create_unicode_buffer(512)
                        n = crypt32.CertGetNameStringW(ctx, kind, 0, para, buf, 512)
                        if n > 1 and buf.value:
                            signers.append(buf.value)
        finally:
            data.dwStateAction = 2     # WTD_STATEACTION_CLOSE
            wintrust.WinVerifyTrust(INVALID, ctypes.byref(action), ctypes.byref(data))
        return {"result": classify_trust_result(ret, last), "code": ret & 0xFFFFFFFF, "signers": signers, "detail": ""}
    except Exception as e:
        return {"result": "error", "code": 0, "signers": [], "detail": str(e)}


def check_installer_signature(cid, path, verifier=None):
    """実行前の署名確認。戻り値: (許可するか, 日本語メッセージ, 署名なしで許可したか)。verifier は差し替え用。"""
    try:
        info = (verifier or verify_authenticode)(path)
    except Exception as e:
        info = {"result": "error", "detail": str(e), "signers": []}
    return evaluate_signature(cid, info)


# ---------------------------------------------------------------------------
# ジョブ (管理者権限のヘルパーが実行する内容)
# ---------------------------------------------------------------------------

def step_register_camera():
    return {"id": "camera", "type": "register_camera", "title": t("setup.wizard.step_register_camera_1")}


def step_exe(cid, title, path, args, ok_codes=(0, 3010, 1641), timeout=900, retry_args=None, lenient=False, reboot=False,
             stage_dir=None):
    # stage_dir: 同じフォルダのほかのファイル (ドライバなど) も必要なインストーラーは、フォルダごと安全な場所へコピーして実行する
    return {"id": cid, "type": "exe", "title": title, "path": path, "args": list(args), "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout, "retry_args": retry_args, "lenient": lenient, "reboot": reboot,
            "stage_dir": stage_dir}


def step_msi(cid, title, path, ok_codes=(0, 3010, 1641), timeout=900, stage_dir=None):
    return {"id": cid, "type": "msi", "title": title, "path": path, "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout, "stage_dir": stage_dir}


def step_winget(cid, title, pkg_id, timeout=900):
    return {"id": cid, "type": "winget", "title": title, "pkg": pkg_id, "timeout": timeout}


def build_job(steps, result_path, progress_path):
    return {"version": 1, "result": result_path, "progress": progress_path, "steps": list(steps)}


def winget_args(pkg_id):
    return ["winget", "install", "--id", pkg_id, "-e", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]


# winget が「すでに導入済み / 更新なし」を返すときのコード
WINGET_OK_CODES = (0, 0x8A150061, 0x8A15002B)


def _write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_progress(path, text):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def _run_cmd(runner, args, timeout):
    flags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    kw = {"timeout": timeout}
    if flags:
        kw["creationflags"] = flags
    return runner(args, **kw).returncode


def _stage_for_run(path, stage_dir=None):
    """管理者権限で実行するファイルを、一般ユーザーが書き換えられない場所 (ProgramData 以下に管理者が作った
    フォルダ) へコピーする。確認 (SHA-256・署名) と実行の間に差し替えられるのを防ぐため。
    戻り値: (実行するパス, 後始末する関数)。Windows 以外 (テスト) ではそのまま使う。"""
    if os.name != "nt":
        return path, (lambda: None)
    import uuid
    base = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "PhoneCam", "run-" + uuid.uuid4().hex)
    os.makedirs(base)
    if stage_dir and os.path.isdir(stage_dir):
        src_root = os.path.abspath(stage_dir)
        dst_root = os.path.join(base, "pkg")
        shutil.copytree(src_root, dst_root)
        rel = os.path.relpath(os.path.abspath(path), src_root)
        if rel.startswith(".."):
            raise RuntimeError(t("setup.wizard.stage_for_run_1"))
        run_path = os.path.join(dst_root, rel)
    else:
        run_path = os.path.join(base, os.path.basename(path))
        shutil.copy2(path, run_path)
    return run_path, (lambda: shutil.rmtree(base, ignore_errors=True))


def _msiexec_path():
    if os.name == "nt":
        return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "msiexec.exe")
    return "msiexec"


# winget で入れてよいパッケージ (ジョブファイルに別のIDが書かれていても実行しない)
ALLOWED_WINGET = frozenset(v["winget"] for v in SOURCES.values() if v.get("winget"))


def explain_exit_code(code, cid=None):
    """インストーラーの終了コード → 「何が起きたか + 次にやること」の1文 (番号は含めない。番号は「詳細」に出す)。"""
    c = (code if code is not None else 1) & 0xFFFFFFFF
    if c in (1602, 1223):
        return t("setup.wizard.explain_exit_code_1")
    if c == 1603:
        msg = t("setup.wizard.explain_exit_code_2")
        if cid in STORE_PACKAGES:
            msg += t("setup.wizard.explain_exit_code_3")
        return msg
    if c == 1618:
        return t("setup.wizard.explain_exit_code_4")
    if c in (1619, 1620):
        return t("setup.wizard.explain_exit_code_5")
    if c in (5, 740, 1260):
        return t("setup.wizard.explain_exit_code_6")
    if c == 1638:
        return t("setup.wizard.explain_exit_code_7")
    return t("setup.wizard.explain_exit_code_8")


def _run_installer(step, kind, path, res, runner, verifier):
    """コピー済みのインストーラー (path) を、SHA-256 → 電子署名の順に確認して実行する。"""
    if sha256_file(path) != step["sha256"]:
        res["detail"] = t("setup.wizard.run_installer_1")
        return res
    allowed, message, unsigned = check_installer_signature(step.get("id"), path, verifier)
    res["signature"] = message
    res["unsigned"] = unsigned
    if not allowed:
        res["detail"] = message
        return res
    if kind == "msi":
        cmd = [_msiexec_path(), "/i", path, "/qn", "/norestart"]
    else:
        cmd = [path] + list(step.get("args") or [])
    code = _run_cmd(runner, cmd, step.get("timeout", 900))
    ok_codes = [c & 0xFFFFFFFF for c in step.get("ok_codes", [0])]
    if (code & 0xFFFFFFFF) not in ok_codes and kind == "exe" and step.get("retry_args") is not None:
        code = _run_cmd(runner, [path] + list(step["retry_args"]), step.get("timeout", 900))
    res["code"] = code
    res["ok"] = (code & 0xFFFFFFFF) in ok_codes or bool(step.get("lenient"))
    res["reboot"] = (code & 0xFFFFFFFF) in EXIT_REBOOT or bool(step.get("reboot"))
    if not res["ok"]:
        res["detail"] = explain_exit_code(code, step.get("id"))
        res["technical"] = t("setup.wizard.run_installer_2") % code
    return res


def execute_step(step, runner=None, verifier=None):
    """1ステップを実行して結果 dict を返す (例外は失敗として返す)。"""
    runner = runner or subprocess.run
    res = {"id": step.get("id"), "title": step.get("title", ""), "ok": False, "code": None, "reboot": False, "detail": ""}
    try:
        kind = step.get("type")
        if kind == "register_camera":
            ok = bool(_call("register_camera"))
            res["ok"] = ok
            res["code"] = 0 if ok else 1
            if not ok:
                res["detail"] = t("setup.wizard.execute_step_1")
            return res
        if kind in ("exe", "msi"):
            path = step["path"]
            if not os.path.isfile(path):
                res["detail"] = t("setup.wizard.execute_step_2")
                return res
            if not step.get("sha256"):
                res["detail"] = t("setup.wizard.execute_step_3")
                return res
            # 一般ユーザーが触れない場所へコピーしてから、その実物を確認して実行する
            path, cleanup = _stage_for_run(path, step.get("stage_dir"))
            try:
                return _run_installer(step, kind, path, res, runner, verifier)
            finally:
                cleanup()
        if kind == "winget":
            if step.get("pkg") not in ALLOWED_WINGET:
                res["detail"] = t("setup.wizard.execute_step_4")
                return res
            code = _run_cmd(runner, winget_args(step["pkg"]), step.get("timeout", 900))
            res["code"] = code
            res["ok"] = (code & 0xFFFFFFFF) in WINGET_OK_CODES
            res["reboot"] = (code & 0xFFFFFFFF) in EXIT_REBOOT
            if not res["ok"]:
                res["detail"] = t("setup.wizard.execute_step_5")
                res["technical"] = t("setup.wizard.execute_step_6") % code
            return res
        res["detail"] = t("setup.wizard.execute_step_7")
        res["technical"] = "type=%s" % kind
    except subprocess.TimeoutExpired:
        res["detail"] = t("setup.wizard.execute_step_8")
    except Exception as e:
        res["detail"] = t("setup.wizard.execute_step_9")
        res["technical"] = str(e)
    return res


def run_job(job_path, expected_sha=None, runner=None, verifier=None):
    """管理者権限で実行される --setup。結果を result.json へ書き、終了コードを返す。
    0 = 全て成功 / 3010 = 成功・要再起動 / 1 = 失敗あり。"""
    result = {"steps": [], "error": ""}
    result_path = None
    try:
        # 管理者権限で何を実行するかは job.json で決まるので、呼び出し側が渡したハッシュと一致するものだけ実行する
        # (ハッシュが渡されていない場合も実行しない)
        if not expected_sha:
            raise RuntimeError(t("setup.wizard.run_job_1"))
        if sha256_file(job_path).lower() != str(expected_sha).strip().lower():
            raise RuntimeError(t("setup.wizard.run_job_2"))
        with open(job_path, "r", encoding="utf-8") as f:
            job = json.load(f)
        result_path = job.get("result") or os.path.join(os.path.dirname(os.path.abspath(job_path)), "result.json")
        progress = job.get("progress")
        steps = job.get("steps") or []
        total = len(steps)
        for i, step in enumerate(steps):
            if progress:
                _append_progress(progress, "%d|%d|%s" % (i + 1, total, step.get("title", "")))
            result["steps"].append(execute_step(step, runner=runner, verifier=verifier))
            if progress:
                _append_progress(progress, "%d|%d|done" % (i + 1, total))
    except Exception as e:
        result["error"] = t("setup.wizard.run_job_3") % e
    try:
        if result_path is None:
            result_path = os.path.join(os.path.dirname(os.path.abspath(job_path)), "result.json")
        _write_json(result_path, result)
    except Exception:
        pass
    if result["error"] or any(not s["ok"] for s in result["steps"]):
        return 1
    return 3010 if any(s["reboot"] for s in result["steps"]) else 0


# ---------------------------------------------------------------------------
# 手順つきインストール (guided): 公式のインストーラーを画面に出して、手順を案内する
# ---------------------------------------------------------------------------
# 自動 (無人) でも確実なものだけを自動にする: VC++ ランタイム / PhoneCamカメラの登録 (と Store 版の削除)。
# iTunes / iCloud / AltServer / VB-CABLE は、公式のインストーラーを普通のウィンドウで開き、手順を画面に出す。

AUTO_IDS = ("vcredist", "camera")
GUIDED_IDS = ("itunes", "icloud", "altserver", "vbcable")
SLOW_SECONDS = 180          # インストーラーがこれより長く動いているとき、隠れていないか確認を促す
AUTO_CHECK_SECONDS = 5.0    # インストーラーの実行中に、導入済みになったか裏で確かめる間隔
SLOW_HINT = t("setup.wizard.slow_hint_1")
MISSING_COMMON = (t("setup.wizard.missing_common_1"))

GUIDE = {
    "itunes": {
        "steps": [t("setup.wizard.guide_1"),
                  t("setup.wizard.guide_2"),
                  t("setup.wizard.guide_3"),
                  t("setup.wizard.guide_4")],
        "note": t("setup.wizard.guide_5"),
        "missing": t("setup.wizard.guide_6"),
    },
    "icloud": {
        "steps": [t("setup.wizard.guide_7"),
                  t("setup.wizard.guide_8"),
                  t("setup.wizard.guide_3"),
                  t("setup.wizard.guide_9")],
        "note": t("setup.wizard.guide_10"),
        "missing": t("setup.wizard.guide_11"),
    },
    "altserver": {
        "steps": [t("setup.wizard.guide_12"),
                  t("setup.wizard.guide_13"),
                  t("setup.wizard.guide_3"),
                  t("setup.wizard.guide_14"),
                  t("setup.wizard.guide_15")],
        "note": t("setup.wizard.guide_16"),
        "missing": t("setup.wizard.guide_17"),
    },
    "vbcable": {
        "steps": [t("setup.wizard.guide_18"),
                  t("setup.wizard.guide_3"),
                  t("setup.wizard.guide_19"),
                  t("setup.wizard.guide_20"),
                  t("setup.wizard.guide_21")],
        "note": t("setup.wizard.guide_22"),
        "missing": t("setup.wizard.guide_23"),
    },
}


def split_ids(ids):
    """選んだ項目を (自動で入れるもの, 手順つきで入れるもの) に分ける。"""
    auto = [c for c in ids if c not in GUIDED_IDS]
    guided = [c for c in ids if c in GUIDED_IDS]
    return auto, guided


def missing_words(cid, status):
    """検出で見つからなかったときの、何が足りないかの説明 (平易な文)。"""
    name = COMPONENT_BY_ID.get(cid, {}).get("name", cid)
    g = GUIDE.get(cid, {})
    detail = (status or {}).get("detail") or ""
    if (status or {}).get("state") == STATE_WARN and detail:
        head = t("setup.wizard.missing_words_1") % (name, detail)
    else:
        head = t("setup.wizard.sentence_fmt") % g.get("missing", t("setup.wizard.missing_words_2") % name)
    return head + "\n" + MISSING_COMMON


class UserDeclined(Exception):
    """インストーラーの管理者確認 (UAC) で「いいえ」が押された。"""


class LaunchedProcess:
    """起動したインストーラー。alive() は 実行中=True / 終了=False / 追跡できない=None。"""

    def __init__(self, handle=None, kernel32=None):
        self.handle = handle
        self._k = kernel32

    def alive(self):
        if not self.handle or self._k is None:
            return None
        try:
            return self._k.WaitForSingleObject(self.handle, 0) == 0x102  # WAIT_TIMEOUT
        except Exception:
            return None

    def close(self):
        if self.handle and self._k is not None:
            try:
                self._k.CloseHandle(self.handle)
            except Exception:
                pass
        self.handle = None


def shell_open(path, args=None, cwd=None):
    """インストーラーを「普通のウィンドウ」で開く (ShellExecute の既定の動作)。インストーラーが管理者権限を
    求めるときだけ、そのインストーラー自身の UAC が出る。戻り値: LaunchedProcess。
    UAC で「いいえ」なら UserDeclined、起動できなければ OSError。"""
    if os.name != "nt":
        raise OSError(t("setup.wizard.shell_open_1"))
    import ctypes

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_uint32), ("hwnd", ctypes.c_void_p),
                    ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                    ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                    ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SEI)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    info = SEI()
    info.cbSize = ctypes.sizeof(SEI)
    info.fMask = 0x00000040      # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = None           # 既定 (open)。runas は付けない
    info.lpFile = path
    info.lpParameters = subprocess.list2cmdline(list(args)) if args else None
    info.lpDirectory = cwd or os.path.dirname(os.path.abspath(path))
    info.nShow = 1               # SW_SHOWNORMAL
    ctypes.set_last_error(0)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:
            raise UserDeclined()
        raise OSError(err, t("setup.wizard.shell_open_2") % err)
    return LaunchedProcess(info.hProcess, kernel32)


class GuidedFlow:
    """手順つきインストールの状態機械 (画面に依存しない)。複数の項目を順に進める。

    phase: preparing (ダウンロード・署名確認など) → ready (「インストーラーを開く」待ち) → running (インストーラー実行中)
           → checking (導入済みか確認中) → [done の次の項目へ | missing (見つからない) ]
           失敗は failed (次にやることつきのメッセージ)。全項目が終わると finished = True。
    prepare / launch / check は呼んだスレッドで実行する (UI は別スレッドで prepare と check を呼ぶ)。"""

    PREPARING, READY, RUNNING, CHECKING, MISSING, FAILED = "preparing", "ready", "running", "checking", "missing", "failed"

    def __init__(self, ids, directory, downloader=None, verifier=None, launcher=None, detector=None,
                 store_remover=None, store_check=None, clock=None):
        self.ids = [c for c in ids if c in GUIDED_IDS]
        self.dir = directory
        self.downloader = downloader or download_file
        self.verifier = verifier
        self.launcher = launcher or shell_open
        self.detector = detector or detect_all
        self.store_remover = store_remover or remove_store_package
        self.store_check = store_check or store_package_installed
        self.clock = clock or time.monotonic
        self.index = 0
        self.phase = self.PREPARING
        self.finished = not self.ids
        self.message = ""        # 利用者に見せる説明 (次にやることつき)
        self.technical = ""      # 「詳細」に出す内容
        self.notice = ""         # 補足 (署名なしの注意など)
        self.job = None          # {"path", "args", "cwd", "sha256"}
        self.proc = None
        self.launched_at = None
        self.detected = False    # 実行中に導入済みを検出できた
        self.results = {}        # cid -> {"ok", "skipped", "detail"}
        self.statuses = {}       # 最後の検出結果
        self.reboot = False
        self._last_auto = 0.0
        self._exit_checked = False

    # -- 状態 -------------------------------------------------------------
    @property
    def current(self):
        return self.ids[self.index] if 0 <= self.index < len(self.ids) and not self.finished else None

    def name(self, cid=None):
        cid = cid or self.current
        return COMPONENT_BY_ID.get(cid, {}).get("name", cid or "")

    def page_url(self, cid=None):
        return SOURCES.get(cid or self.current, {}).get("page", "")

    def elapsed(self):
        return 0.0 if self.launched_at is None else max(0.0, self.clock() - self.launched_at)

    def is_slow(self):
        return self.phase == self.RUNNING and self.elapsed() >= SLOW_SECONDS

    def _fail(self, message, technical=""):
        self.phase = self.FAILED
        self.message = message
        self.technical = technical

    def _advance(self):
        self.index += 1
        self.job = None
        self.proc = None
        self.launched_at = None
        self.detected = False
        self._exit_checked = False
        self.message = self.technical = self.notice = ""
        if self.index >= len(self.ids):
            self.finished = True
            self.phase = self.PREPARING
        else:
            self.phase = self.PREPARING

    def _finish_ok(self, cid, detail=""):
        self.results[cid] = {"ok": True, "skipped": False, "detail": detail}
        self._advance()

    def skip(self):
        cid = self.current
        if cid is None:
            return
        self._close_proc()
        self.results[cid] = {"ok": False, "skipped": True,
                             "detail": t("setup.wizard.guidedflow_skip_1")}
        self._advance()

    def abort(self):
        """残りをすべて「あとで」にして終わる。"""
        while not self.finished:
            self.skip()

    def _close_proc(self):
        if self.proc is not None:
            try:
                self.proc.close()
            except Exception:
                pass

    # -- 準備: 確認 → Store 版の削除 → ダウンロード → 署名確認 ------------------
    def prepare(self, emit=None, cancel=None):
        emit = emit or (lambda kind, **kw: None)
        cid = self.current
        if cid is None or self.finished:
            return
        name = self.name(cid)
        self.phase = self.PREPARING
        self.message = self.technical = self.notice = ""
        try:
            self.statuses = self.detector()
        except Exception:
            self.statuses = {}
        if self.statuses.get(cid, {}).get("state") == STATE_OK:
            emit("log", text=t("setup.wizard.guidedflow_prepare_1") % name)
            self._finish_ok(cid, t("setup.wizard.guidedflow_prepare_2"))
            return
        if cid in STORE_PACKAGES and self.store_check(cid):
            emit("stage", text=t("setup.wizard.guidedflow_prepare_3") % name)
            ok, msg = self.store_remover(cid)
            emit("log", text="%s: %s" % (name, msg))
            if not ok:
                self._fail(msg)
                return
        src = SOURCES[cid]
        dest = os.path.join(self.dir, src["file"])

        def prog(done, total, _n=name):
            if total:
                emit("stage", text=t("setup.wizard.prepare_prog_1") % (_n, _mb(done), _mb(total)))
                emit("progress", value=min(1.0, done / float(total)))
            else:
                emit("stage", text=t("setup.wizard.prepare_prog_2") % (_n, _mb(done)))

        emit("stage", text=t("setup.wizard.guidedflow_prepare_4") % name)
        emit("progress", value=0.0)
        try:
            self.downloader(src["url"], dest, src["kind"], progress=prog, cancel=cancel)
            emit("log", text=t("setup.wizard.guidedflow_prepare_5") % name)
            emit("stage", text=t("setup.wizard.guidedflow_prepare_6") % name)
            job = self._locate(cid, dest)
            allowed, msg, unsigned = check_installer_signature(cid, job["path"], self.verifier)
            emit("log", text="%s: %s" % (name, msg))
            if not allowed:
                self._fail(msg)
                return
            if unsigned:
                self.notice = (t("setup.wizard.guidedflow_prepare_7"))
            job["sha256"] = sha256_file(job["path"])
        except DownloadError as e:
            self._fail(str(e), getattr(e, "technical", ""))
            return
        except Exception as e:
            self._fail(t("setup.wizard.guidedflow_prepare_8"), str(e))
            return
        self.job = job
        self.phase = self.READY
        emit("stage", text=t("setup.wizard.guidedflow_prepare_9") % name)
        emit("progress", value=1.0)

    def _locate(self, cid, dest):
        """ダウンロードしたファイルから、開くインストーラー {"path", "args", "cwd"} を決める。"""
        if cid == "altserver":
            root = safe_extract_zip(dest, os.path.join(self.dir, "altserver"))
            exe = find_file(root, ["setup.exe"])
            if exe:
                return {"path": exe, "args": [], "cwd": os.path.dirname(exe)}
            msi = find_file(root, ["setup.msi", "altserver.msi", "altinstaller.msi"])
            if msi:
                return {"path": msi, "args": [], "cwd": os.path.dirname(msi), "msi": True}
            raise DownloadError(t("setup.wizard.guidedflow__locate_1"))
        if cid == "vbcable":
            root = safe_extract_zip(dest, os.path.join(self.dir, "vbcable"))
            exe = find_file(root, ["vbcable_setup_x64.exe", "vbcable_setup.exe"])
            if not exe:
                raise DownloadError(t("setup.wizard.guidedflow__locate_1"))
            return {"path": exe, "args": [], "cwd": os.path.dirname(exe)}
        return {"path": dest, "args": [], "cwd": os.path.dirname(dest)}

    # -- 開く -------------------------------------------------------------
    def launch(self):
        """「インストーラーを開く」。確認済みのファイルのまま (変更されていない) か確かめてから、普通のウィンドウで開く。"""
        cid = self.current
        if cid is None or self.job is None or self.phase not in (self.READY, self.RUNNING, self.MISSING):
            return False
        job = self.job
        try:
            if sha256_file(job["path"]) != job.get("sha256"):
                self._fail(t("setup.wizard.guidedflow_launch_1"))
                return False
        except Exception:
            self._fail(t("setup.wizard.guidedflow_launch_2"))
            return False
        self._close_proc()
        try:
            if job.get("msi"):
                self.proc = self.launcher(_msiexec_path(), ["/i", job["path"]], job.get("cwd"))
            else:
                self.proc = self.launcher(job["path"], job.get("args") or [], job.get("cwd"))
        except UserDeclined:
            self.message = t("setup.wizard.guidedflow_launch_3")
            self.phase = self.READY
            return False
        except Exception as e:
            self.proc = None
            self._fail(t("setup.wizard.guidedflow_launch_4"), str(e))
            return False
        self.message = ""
        self.phase = self.RUNNING
        self.launched_at = self.clock()
        self._last_auto = self.launched_at
        self._exit_checked = False
        self.detected = False
        return True

    # -- 確認 -------------------------------------------------------------
    def process_alive(self):
        return self.proc.alive() if self.proc is not None else None

    def needs_auto_check(self):
        """実行中に、裏で確認すべきか (インストーラーが終わった / 一定時間たった)。"""
        if self.phase not in (self.RUNNING, self.MISSING) or self.finished:
            return False
        if self.phase == self.RUNNING and self.process_alive() is False and not self._exit_checked:
            return True
        return self.clock() - self._last_auto >= AUTO_CHECK_SECONDS

    def check(self, auto=False):
        """導入済みか確認する。戻り値: "done" (次へ進んだ) / "missing" (見つからない) / "running" (まだ実行中)。
        auto=True は裏での確認 (見つからなくても画面に何も出さない)。"""
        cid = self.current
        if cid is None:
            return "done"
        self._last_auto = self.clock()
        alive = self.process_alive()
        if alive is False:
            self._exit_checked = True
        if not auto:
            self.phase = self.CHECKING
        try:
            self.statuses = self.detector()
        except Exception:
            self.statuses = {}
        st = self.statuses.get(cid, {})
        if st.get("state") == STATE_OK:
            if auto and alive is not False:
                # インストーラーがまだ開いている (または追跡できない)。閉じる / 「確認」を押すと次へ進む
                self.detected = True
                return "running"
            if cid == "vbcable":
                self.reboot = True
            self._close_proc()
            self._finish_ok(cid)
            return "done"
        if alive:
            if not auto:
                self.phase = self.RUNNING
                self.message = t("setup.wizard.guidedflow_check_1")
            return "running"
        if auto and alive is None and self.phase == self.RUNNING:
            return "running"   # 追跡できないときは、利用者が「確認」を押すまで待つ
        if auto and self.phase == self.MISSING:
            return "missing"
        self.phase = self.MISSING
        self.message = missing_words(cid, st)
        return "missing"

    def retry(self):
        """最初 (ダウンロード) からやり直す。"""
        if self.finished:
            return
        self._close_proc()
        self.proc = None
        self.phase = self.PREPARING
        self.message = self.technical = ""

    def summary(self):
        return dict(self.results)


# ---------------------------------------------------------------------------
# AltServer の診断 (エラー 2003 など「iPhoneが見つからない」ときの原因探し)
# ---------------------------------------------------------------------------
# 外部コマンドはシェルを通さず、引数のリストで実行する。出力の解析はコマンドの実行と切り離してあり、
# 実行関数 (run) を差し替えてテストできる。日本語/英語どちらの Windows でも読めるよう、文言ではなく構造で判定する。

DIAG_OK, DIAG_NG, DIAG_WARN = "ok", "ng", "warn"
VPN_KEYWORDS = ("tailscale", "wireguard", "openvpn", "nordlynx", "zerotier", "warp", "wintun", "tap-windows",
                "expressvpn", "protonvpn", "mullvad", "vpn", "cisco anyconnect", "forticlient", "globalprotect")
NET_PRIVATE, NET_PUBLIC, NET_DOMAIN = "private", "public", "domain"


def run_text(cmd, timeout=20):
    """コマンドを (ウィンドウなし・shell なし) で実行して (終了コード, 出力文字列) を返す。失敗は (None, "")。"""
    try:
        kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "timeout": timeout}
        if os.name == "nt":
            kw["creationflags"] = _CREATE_NO_WINDOW
        r = subprocess.run(list(cmd), **kw)
        raw = r.stdout or b""
        text = None
        for enc in (("utf-8",) if cmd and str(cmd[0]).lower().startswith("powershell") else ()) + ("oem", "mbcs", "cp932", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        return r.returncode, text if text is not None else raw.decode("utf-8", "replace")
    except Exception:
        return None, ""


def parse_tasklist_has(stdout, image):
    """tasklist /FO CSV /NH の出力に image (例: AltServer.exe) のプロセスがあるか。"""
    return ('"%s"' % image).lower() in (stdout or "").lower()


def parse_sc_state(rc, stdout):
    """sc query の結果 → "running" / "stopped" / "missing" (サービスなし) / "unknown"。"""
    text = stdout or ""
    if rc == 1060:
        return "missing"
    # 「STATE : 4  RUNNING」(日本語の Windows では「状態 : 4  RUNNING」)。TYPE の行と混ざらないよう状態名で探す
    m = re.search(r":\s*(\d+)\s+(RUNNING|STOPPED|START_PENDING|STOP_PENDING|CONTINUE_PENDING|PAUSE_PENDING|PAUSED)\b", text)
    if m:
        return "running" if m.group(2) == "RUNNING" else "stopped"
    if "1060" in text:
        return "missing"
    return "unknown"


def _net_category(v):
    if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
        return {0: NET_PUBLIC, 1: NET_PRIVATE, 2: NET_DOMAIN}.get(int(v), "")
    t = str(v or "").strip().lower()
    if t.startswith("public"):
        return NET_PUBLIC
    if t.startswith("private"):
        return NET_PRIVATE
    if t.startswith("domain"):
        return NET_DOMAIN
    return ""


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_network_json(stdout):
    """PowerShell の JSON 出力 → ({"profiles": [{name, alias, category}], "adapters": [{name, desc}]}) または None。"""
    text = (stdout or "").strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(text[i:j + 1])
    except Exception:
        return None
    profiles = [{"name": str(p.get("name") or ""), "alias": str(p.get("alias") or ""), "category": _net_category(p.get("cat"))}
                for p in _as_list(data.get("profiles")) if isinstance(p, dict)]
    adapters = [{"name": str(a.get("name") or ""), "desc": str(a.get("desc") or "")}
                for a in _as_list(data.get("adapters")) if isinstance(a, dict)]
    return {"profiles": profiles, "adapters": adapters}


def find_vpn_adapters(adapters):
    out = []
    for a in adapters or []:
        text = ("%s %s" % (a.get("name", ""), a.get("desc", ""))).lower()
        if any(k in text for k in VPN_KEYWORDS):
            out.append(a.get("name") or a.get("desc") or "")
    return out


def parse_firewall(stdout, keyword="altserver"):
    """netsh advfirewall firewall show rule name=all の出力から、keyword を含むルールを調べる。
    戻り値: {"found": 件数, "allow_in": 有効な受信許可があるか, "block": 有効なブロックがあるか}。
    項目名は英語/日本語の両方を読む。ルールは空行で区切られる。"""
    res = {"found": 0, "allow_in": False, "block": False}
    for block in re.split(r"\r?\n\s*\r?\n", stdout or ""):
        if keyword.lower() not in block.lower():
            continue
        res["found"] += 1
        enabled = re.search(r"(?:Enabled|有効)\s*:\s*(\S+)", block)
        action = re.search(r"(?:Action|操作)\s*:\s*(\S+)", block)
        direction = re.search(r"(?:Direction|方向)\s*:\s*(\S+)", block)
        if enabled and enabled.group(1).lower() not in ("yes", "はい"):
            continue
        a = (action.group(1).lower() if action else "")
        d = (direction.group(1).lower() if direction else "in")
        if a in ("allow", "許可"):
            if d in ("in", "入", "受信"):
                res["allow_in"] = True
        elif a in ("block", "ブロック"):
            res["block"] = True
    return res


_NET_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "$p=@(Get-NetConnectionProfile | ForEach-Object { @{name=$_.Name; alias=$_.InterfaceAlias; cat=[int]$_.NetworkCategory} }); "
    "$a=@(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { @{name=$_.Name; desc=$_.InterfaceDescription} }); "
    "@{profiles=$p; adapters=$a} | ConvertTo-Json -Compress -Depth 4")


def _diag(did, label, state, detail="", fix=""):
    return {"id": did, "label": label, "state": state, "detail": detail, "fix": fix}


def run_diagnostics(run=None, itunes_status=None, usb_snapshot=None, progress=None):
    """AltServer の診断。戻り値: [{id, label, state(ok/ng/warn), detail, fix}]。
    run(cmd_list, timeout) -> (rc, text) を差し替えてテストできる。itunes_status / usb_snapshot も差し替え可。"""
    run = run or run_text
    out = []

    def note(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    # 1. AltServer.exe が動いているか
    note(t("setup.wizard.run_diagnostics_1"))
    rc, txt = run(["tasklist", "/FI", "IMAGENAME eq AltServer.exe", "/FO", "CSV", "/NH"], 15)
    if rc is None:
        out.append(_diag("altserver", t("setup.wizard.run_diagnostics_2"), DIAG_WARN, t("setup.wizard.run_diagnostics_3"), t("setup.wizard.run_diagnostics_4")))
    elif parse_tasklist_has(txt, "AltServer.exe"):
        out.append(_diag("altserver", t("setup.wizard.run_diagnostics_2"), DIAG_OK))
    else:
        out.append(_diag("altserver", t("setup.wizard.run_diagnostics_2"), DIAG_NG, t("setup.wizard.run_diagnostics_5"),
                         t("setup.wizard.run_diagnostics_6")))

    # 2. Apple Mobile Device Service / 3. Bonjour Service
    for did, label, svc, fix_missing, fix_stopped in (
            ("amds", t("setup.wizard.run_diagnostics_7"), "Apple Mobile Device Service",
             t("setup.wizard.run_diagnostics_8"),
             t("setup.wizard.run_diagnostics_9")),
            ("bonjour", t("setup.wizard.run_diagnostics_10"), "Bonjour Service",
             t("setup.wizard.run_diagnostics_11"),
             t("setup.wizard.run_diagnostics_12"))):
        note(t("setup.wizard.run_diagnostics_13") % svc)
        rc, txt = run(["sc.exe", "query", svc], 15)
        state = "unknown" if rc is None else parse_sc_state(rc, txt)
        if state == "running":
            out.append(_diag(did, label, DIAG_OK))
        elif state == "stopped":
            out.append(_diag(did, label, DIAG_NG, t("setup.wizard.run_diagnostics_14"), fix_stopped))
        elif state == "missing":
            out.append(_diag(did, label, DIAG_NG, t("setup.wizard.run_diagnostics_15"), fix_missing))
        else:
            out.append(_diag(did, label, DIAG_WARN, t("setup.wizard.run_diagnostics_3"), t("setup.wizard.run_diagnostics_4")))

    # 4. 公式サイト版 iTunes
    note(t("setup.wizard.run_diagnostics_16"))
    try:
        st = itunes_status() if itunes_status else detect_itunes()
    except Exception:
        st = {"state": STATE_WARN, "detail": t("setup.wizard.run_diagnostics_3")}
    if st.get("state") == STATE_OK:
        out.append(_diag("itunes", t("setup.wizard.run_diagnostics_17"), DIAG_OK))
    elif st.get("state") == STATE_WARN:
        out.append(_diag("itunes", t("setup.wizard.run_diagnostics_17"), DIAG_NG, st.get("detail", ""),
                         t("setup.wizard.run_diagnostics_18")))
    else:
        out.append(_diag("itunes", t("setup.wizard.run_diagnostics_17"), DIAG_NG, t("setup.wizard.run_diagnostics_19"),
                         t("setup.wizard.run_diagnostics_20")))

    # 5. iPhone が USB でつながっている (+ 信頼)
    note(t("setup.wizard.run_diagnostics_21"))
    try:
        snap = usb_snapshot() if usb_snapshot else (iphone.query_usbmux() if iphone is not None else {})
    except Exception:
        snap = {}
    if snap.get("usb"):
        if snap.get("trusted") is False:
            out.append(_diag("usb", t("setup.wizard.run_diagnostics_22"), DIAG_NG, t("setup.wizard.run_diagnostics_23"),
                             t("setup.wizard.run_diagnostics_24")))
        else:
            out.append(_diag("usb", t("setup.wizard.run_diagnostics_22"), DIAG_OK))
    else:
        out.append(_diag("usb", t("setup.wizard.run_diagnostics_22"), DIAG_NG, t("setup.wizard.run_diagnostics_25"),
                         t("setup.wizard.run_diagnostics_26")))

    # 6. ネットワークの種類 / 7. VPN
    note(t("setup.wizard.run_diagnostics_27"))
    rc, txt = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _NET_SCRIPT], 30)
    net = parse_network_json(txt) if rc is not None else None
    if net is None:
        out.append(_diag("network", t("setup.wizard.run_diagnostics_28"), DIAG_WARN, t("setup.wizard.run_diagnostics_3"),
                         t("setup.wizard.run_diagnostics_29")))
        out.append(_diag("vpn", t("setup.wizard.run_diagnostics_30"), DIAG_WARN, t("setup.wizard.run_diagnostics_3"), t("setup.wizard.run_diagnostics_31")))
    else:
        publics = [p for p in net["profiles"] if p["category"] == NET_PUBLIC]
        others = [p for p in net["profiles"] if p["category"] in (NET_PRIVATE, NET_DOMAIN)]
        fix_net = (t("setup.wizard.run_diagnostics_32"))
        if not net["profiles"]:
            out.append(_diag("network", t("setup.wizard.run_diagnostics_28"), DIAG_WARN, t("setup.wizard.run_diagnostics_33"),
                             t("setup.wizard.run_diagnostics_34")))
        elif publics and not others:
            out.append(_diag("network", t("setup.wizard.run_diagnostics_28"), DIAG_NG,
                             t("setup.wizard.run_diagnostics_35") % t("setup.wizard.detect_itunes_1").join(p["name"] or p["alias"] for p in publics), fix_net))
        elif publics:
            out.append(_diag("network", t("setup.wizard.run_diagnostics_28"), DIAG_WARN,
                             t("setup.wizard.run_diagnostics_36") % t("setup.wizard.detect_itunes_1").join(p["name"] or p["alias"] for p in publics),
                             t("setup.wizard.run_diagnostics_37") + fix_net))
        else:
            out.append(_diag("network", t("setup.wizard.run_diagnostics_28"), DIAG_OK))
        vpns = find_vpn_adapters(net["adapters"])
        if vpns:
            out.append(_diag("vpn", t("setup.wizard.run_diagnostics_30"), DIAG_WARN, t("setup.wizard.run_diagnostics_38") % t("setup.wizard.detect_itunes_1").join(vpns),
                             t("setup.wizard.run_diagnostics_39")))
        else:
            out.append(_diag("vpn", t("setup.wizard.run_diagnostics_30"), DIAG_OK))

    # 8. ファイアウォール
    note(t("setup.wizard.run_diagnostics_40"))
    rc, txt = run(["netsh", "advfirewall", "firewall", "show", "rule", "name=all"], 40)
    if rc is None:
        out.append(_diag("firewall", t("setup.wizard.run_diagnostics_41"), DIAG_WARN, t("setup.wizard.run_diagnostics_3"),
                         t("setup.wizard.run_diagnostics_42")))
    else:
        fw = parse_firewall(txt, "altserver")
        if fw["allow_in"] and not fw["block"]:
            out.append(_diag("firewall", t("setup.wizard.run_diagnostics_41"), DIAG_OK))
        elif fw["block"]:
            out.append(_diag("firewall", t("setup.wizard.run_diagnostics_41"), DIAG_NG, t("setup.wizard.run_diagnostics_43"),
                             t("setup.wizard.run_diagnostics_44")))
        else:
            out.append(_diag("firewall", t("setup.wizard.run_diagnostics_41"), DIAG_NG, t("setup.wizard.run_diagnostics_45"),
                             t("setup.wizard.run_diagnostics_46")))
    note("")
    return out


def diagnostics_summary(results):
    """診断結果の1行まとめ。"""
    ng = [r for r in results if r["state"] == DIAG_NG]
    warn = [r for r in results if r["state"] == DIAG_WARN]
    if not ng and not warn:
        return t("setup.wizard.diagnostics_summary_1")
    parts = []
    if ng:
        parts.append(t("setup.wizard.diagnostics_summary_2") % len(ng))
    if warn:
        parts.append(t("setup.wizard.diagnostics_summary_3") % len(warn))
    return t("setup.wizard.list_sep").join(parts) + t("setup.wizard.diagnostics_summary_4")


# ---------------------------------------------------------------------------
# 依存・選択
# ---------------------------------------------------------------------------

def default_selection(statuses, mic=False):
    """初期のチェック状態。導入済みは False、必須の未導入は True、任意は AltServer だけ True。"""
    sel = {}
    for c in COMPONENTS:
        cid = c["id"]
        st = statuses.get(cid, {}).get("state", STATE_MISSING)
        if st == STATE_OK:
            sel[cid] = False
        elif c["required"]:
            sel[cid] = st == STATE_MISSING
        elif cid == "vbcable":
            sel[cid] = bool(mic)
        else:
            sel[cid] = bool(c.get("default_on"))
    return sel


def is_selectable(cid, statuses):
    """ソース実行時のカメラのようにインストールできない行は False。"""
    if cid == "camera" and not _frozen():
        return False
    st = statuses.get(cid, {}).get("state", STATE_MISSING)
    return st != STATE_OK


def expand_selection(ids, statuses):
    """AltServer を選んだら、足りない iTunes (公式サイト版) と iCloud も追加する。並びはインストール順。"""
    chosen = set(ids)
    if "altserver" in chosen:
        for dep in ("itunes", "icloud"):
            # 「要確認」(Store版など) も公式サイト版に入れ替える
            if statuses.get(dep, {}).get("state") != STATE_OK:
                chosen.add(dep)
    if "camera" in chosen and statuses.get("vcredist", {}).get("state") == STATE_MISSING:
        chosen.add("vcredist")
    return [c for c in INSTALL_ORDER if c in chosen]


# ---------------------------------------------------------------------------
# エンジン
# ---------------------------------------------------------------------------

def _mb(n):
    return "%.1f MB" % (n / 1048576.0)


class SetupEngine:
    """ダウンロード → (1回の管理者確認で) インストール → 再検出。run() はブロックするのでワーカースレッドで呼ぶ。"""

    def __init__(self, directory=None, downloader=None, winget_finder=None, detector=None):
        self.dir = directory or setup_dir()
        self.downloader = downloader or download_file
        self.winget_finder = winget_finder or (lambda: shutil.which("winget"))
        self.store_remover = remove_store_package
        self.detector = detector or detect_all

    # -- 1コンポーネント分のステップを作る ---------------------------------
    def _prepare(self, cid, emit, cancel, progress):
        src = SOURCES[cid]
        dest = os.path.join(self.dir, src["file"])
        emit("stage", text=t("setup.wizard.guidedflow_prepare_4") % COMPONENT_BY_ID[cid]["name"])
        self.downloader(src["url"], dest, src["kind"], progress=progress, cancel=cancel)
        title = t("setup.wizard.setupengine__prepare_1") % COMPONENT_BY_ID[cid]["name"]
        if cid == "altserver":
            root = safe_extract_zip(dest, os.path.join(self.dir, "altserver"))
            msi = find_file(root, ["setup.msi", "altserver.msi", "altinstaller.msi"])
            if msi:
                return step_msi(cid, title, msi, stage_dir=root)
            exe = find_file(root, ["setup.exe"])
            if exe:
                # 無人インストールが効かない場合は、通常のウィンドウ付きでもう一度実行する
                return step_exe(cid, title, exe, ["/quiet", "/norestart"], retry_args=[], stage_dir=root)
            raise DownloadError(t("setup.wizard.setupengine__prepare_2"))
        if cid == "vbcable":
            root = safe_extract_zip(dest, os.path.join(self.dir, "vbcable"))
            exe = find_file(root, ["vbcable_setup_x64.exe", "vbcable_setup.exe"])
            if not exe:
                raise DownloadError(t("setup.wizard.setupengine__prepare_2"))
            # VB-CABLE は終了コードがあてにならないので、導入後の再検出で判定する
            return step_exe(cid, title, exe, ["-i", "-h"], timeout=300, lenient=True, reboot=True, stage_dir=root)
        return step_exe(cid, title, dest, src["args"], ok_codes=src.get("ok_codes", (0, 3010, 1641)))

    def run(self, ids, emit=None, cancel=None):
        emit = emit or (lambda kind, **kw: None)
        out = {"results": {}, "manual": [], "reboot": False, "error": "", "statuses": {}, "ids": list(ids)}
        os.makedirs(self.dir, exist_ok=True)
        total_units = len([c for c in ids if c in SOURCES]) + len(ids)
        total_units = max(total_units, 1)
        state = {"units": 0.0}

        def set_progress(extra=0.0):
            emit("progress", value=min(1.0, (state["units"] + extra) / total_units))

        steps = []
        for cid in ids:
            name = COMPONENT_BY_ID[cid]["name"]
            if cid == "camera":
                steps.append(step_register_camera())
                continue
            if cid not in SOURCES:
                continue

            def prog(done, total, _name=name):
                if total:
                    emit("stage", text=t("setup.wizard.prepare_prog_1") % (_name, _mb(done), _mb(total)))
                    set_progress(done / float(total))
                else:
                    emit("stage", text=t("setup.wizard.prepare_prog_2") % (_name, _mb(done)))

            try:
                steps.append(self._prepare(cid, emit, cancel, prog))
                emit("log", text=t("setup.wizard.guidedflow_prepare_5") % name)
            except Exception as e:
                msg = str(e) if isinstance(e, DownloadError) else t("setup.wizard.setupengine_run_1")
                emit("log", text="%s: %s%s" % (name, msg, (" [%s]" % getattr(e, "technical", "")) if getattr(e, "technical", "")
                                              else ("" if isinstance(e, DownloadError) else " [%s]" % e)))
                src = SOURCES[cid]
                if src.get("winget") and self.winget_finder():
                    emit("log", text=t("setup.wizard.setupengine_run_2") % name)
                    steps.append(step_winget(cid, t("setup.wizard.setupengine_run_3") % name, src["winget"]))
                else:
                    out["results"][cid] = {"ok": False, "detail": msg, "code": None}
                    out["manual"].append({"id": cid, "name": name, "url": src["page"], "text": MANUAL_TEXT.get(cid, "")})
            state["units"] += 1
            set_progress()

        # Store 版は公式サイト版と共存できないので、管理者ジョブの前に通常権限で削除する (Store アプリはユーザー単位のため)
        kept = []
        for st in steps:
            cid = st.get("id")
            if cid in STORE_PACKAGES and store_package_installed(cid):
                name = COMPONENT_BY_ID[cid]["name"]
                emit("stage", text=t("setup.wizard.guidedflow_prepare_3") % name)
                ok, msg = self.store_remover(cid)
                emit("log", text="%s: %s" % (name, msg))
                if not ok:
                    out["results"][cid] = {"ok": False, "detail": msg, "code": None}
                    continue
            kept.append(st)
        steps = kept

        if steps:
            self._run_elevated(steps, out, emit, state, total_units)
        else:
            state["units"] = total_units
        emit("progress", value=1.0)
        emit("stage", text=t("setup.wizard.setupengine_run_4"))
        try:
            out["statuses"] = self.detector()
        except Exception as e:
            out["error"] = out["error"] or t("setup.wizard.setupengine_run_5") % e
        # インストールは成功したのに検出できない場合は「要確認」
        for cid, r in out["results"].items():
            st = out["statuses"].get(cid, {}).get("state")
            if r["ok"] and st is not None and st != STATE_OK and cid != "camera":
                r["detail"] = r["detail"] or t("setup.wizard.setupengine_run_6")
            elif r["ok"] and cid == "camera" and st is not None and st != STATE_OK:
                r["ok"] = False
                r["detail"] = t("setup.wizard.setupengine_run_7")
        if "vbcable" in out["results"] and out["results"]["vbcable"]["ok"]:
            out["reboot"] = True
        return out

    def _run_elevated(self, steps, out, emit, state, total_units):
        job_path = os.path.join(self.dir, "job.json")
        result_path = os.path.join(self.dir, "result.json")
        progress_path = os.path.join(self.dir, "progress.log")
        for p in (result_path, progress_path):
            _silent_remove(p)
        _write_json(job_path, build_job(steps, result_path, progress_path))
        sha = sha256_file(job_path)
        params = '--setup "%s" --sha256 %s' % (job_path, sha)
        if not _frozen():
            script = _host.get("script_path")
            if script:
                params = '"%s" %s' % (script(), params)
        emit("stage", text=t("setup.wizard.setupengine__run_elevated_1"))
        emit("log", text=t("setup.wizard.setupengine__run_elevated_2") % len(steps))
        box = []

        def work():
            try:
                box.append(_call("run_elevated", params, JOB_TIMEOUT_MS))
            except Exception as e:
                box.append(("error", str(e)))

        th = threading.Thread(target=work, name="setup-elevated", daemon=True)
        th.start()
        base_units = state["units"]
        seen = 0
        while th.is_alive():
            th.join(0.4)
            seen = self._poll_progress(progress_path, seen, emit, state, base_units, total_units)
        self._poll_progress(progress_path, seen, emit, state, base_units, total_units)
        kind, code = box[0] if box else ("error", 0)
        result = None
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
        step_by_id = {s["id"]: s for s in steps}
        if result is None:
            if kind == "cancelled":
                out["error"] = t("setup.wizard.setupengine__run_elevated_3")
            elif kind == "timeout":
                out["error"] = t("setup.wizard.setupengine__run_elevated_4")
            else:
                out["error"] = (t("setup.wizard.setupengine__run_elevated_5"))
                emit("log", text=t("setup.wizard.setupengine__run_elevated_6") % (kind, code))
            for cid in step_by_id:
                out["results"][cid] = {"ok": False, "detail": out["error"], "code": None}
            return
        if result.get("error"):
            out["error"] = result["error"]
        for r in result.get("steps", []):
            out["results"][r["id"]] = {"ok": bool(r.get("ok")), "detail": r.get("detail", ""), "code": r.get("code"),
                                       "technical": r.get("technical", "")}
            if r.get("reboot"):
                out["reboot"] = True
            emit("log", text="%s: %s" % (r.get("title", r.get("id")), t("setup.wizard.setupengine__run_elevated_7") if r.get("ok") else (
                t("setup.wizard.setupengine__run_elevated_8") + r.get("detail", "") + ((" [" + r["technical"] + "]") if r.get("technical") else ""))))
        for cid in step_by_id:
            out["results"].setdefault(cid, {"ok": False, "detail": t("setup.wizard.setupengine__run_elevated_9"), "code": None})
        state["units"] = base_units + len(steps)

    @staticmethod
    def _poll_progress(path, seen, emit, state, base_units, total_units):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except Exception:
            return seen
        for line in lines[seen:]:
            parts = line.split("|", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                i, n, title = int(parts[0]), int(parts[1]), parts[2]
                done = i if title == "done" else i - 1
                emit("progress", value=min(1.0, (base_units + done) / float(total_units)))
                if title != "done":
                    emit("stage", text="%s…" % title)
        return len(lines)


# ---------------------------------------------------------------------------
# コントローラー (UI から独立した状態管理)
# ---------------------------------------------------------------------------

class SetupController:
    """検出結果・選択状態・実行状態を持つ。UI は listeners に登録して更新を受ける。"""

    def __init__(self, root, engine=None, detector=None, can_start=None, on_busy=None, on_changed=None,
                 audio_guard=None):
        self.root = root
        self.audio_guard = audio_guard  # VB-CABLE の導入で既定の音声デバイスが変わるのを戻す (phonecam_audio.RestoreGuard)
        self.audio_message = ""
        self.audio_polling = False
        self.engine = engine or SetupEngine()
        self.detector = detector or detect_all
        self.can_start = can_start or (lambda: True)
        self.on_busy = on_busy or (lambda flag: None)
        self.on_changed = on_changed or (lambda: None)
        self.statuses = {}
        self.checked = False
        self.selection = {}
        self.touched = set()
        self.mic = False
        self.busy = False
        self.checking = False
        self.outcome = None
        self.log_lines = []
        self.stage = ""
        self.progress = 0.0
        self.listeners = []
        self.guided = None           # GuidedFlow (手順つきインストール中)
        self.guided_working = False  # prepare / check をスレッドで実行中
        self._guided_guard = None
        self._pending_guided = []
        self.guided_directory = None
        self.guided_factory = None   # テスト用に GuidedFlow の作り方を差し替える
        self.cancel = threading.Event()
        self._q = queue.Queue()
        self._pumping = False
        self.closed = False

    # -- 通知 -------------------------------------------------------------
    def listen(self, fn):
        self.listeners.append(fn)

    def unlisten(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def _notify(self, what):
        for fn in list(self.listeners):
            try:
                fn(what)
            except Exception:
                pass

    # -- スレッド → UI -----------------------------------------------------
    def _arm(self):
        if self._pumping or self.closed:
            return
        self._pumping = True
        try:
            self.root.after(100, self._tick)
        except Exception:
            self._pumping = False

    def _tick(self):
        self._pumping = False
        self.pump()
        if (self.busy or self.checking or self.audio_polling or self.guided_working) and not self.closed:
            self._arm()

    def pump(self):
        """キューの内容を UI スレッドで処理する。"""
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(kind, payload)
            except Exception:
                pass

    def _handle(self, kind, payload):
        if kind == "stage":
            self.stage = payload
            self._notify("stage")
        elif kind == "progress":
            self.progress = payload
            self._notify("progress")
        elif kind == "log":
            self.log_lines.append(payload)
            self._notify("log")
        elif kind == "checked":
            self.statuses = payload
            self.checked = True
            self.checking = False
            self.apply_defaults()
            self._notify("statuses")
            self.on_changed()
        elif kind == "audio":
            self.audio_polling = False
            self.audio_message = (payload or {}).get("message", "")
            self._notify("audio")
        elif kind == "guided":
            self._guided_updated()
        elif kind == "done":
            self.busy = False
            self.outcome = payload
            if payload.get("statuses"):
                self.statuses = payload["statuses"]
                self.apply_defaults()
            self.on_busy(False)
            self._notify("done")
            self._notify("statuses")
            self.on_changed()
            if self._pending_guided:
                ids, self._pending_guided = self._pending_guided, []
                self.guided_begin(ids)

    # -- 検出 -------------------------------------------------------------
    def recheck(self):
        if self.checking or self.busy:
            return
        self.checking = True
        self._notify("checking")

        def work():
            try:
                res = self.detector()
            except Exception as e:
                res = {c["id"]: {"state": STATE_WARN, "detail": t("setup.wizard.detect_all_1")} for c in COMPONENTS}
            self._q.put(("checked", res))

        threading.Thread(target=work, name="setup-detect", daemon=True).start()
        self._arm()

    # -- 選択 -------------------------------------------------------------
    def apply_defaults(self):
        base = default_selection(self.statuses, self.mic)
        for cid, v in base.items():
            locked = COMPONENT_BY_ID[cid]["required"] and v
            if cid in self.touched and not locked and self.statuses.get(cid, {}).get("state") != STATE_OK:
                continue  # 利用者が触った任意項目はそのまま
            self.selection[cid] = v

    def set_selected(self, cid, value):
        self.touched.add(cid)
        self.selection[cid] = bool(value)

    def set_mic(self, flag):
        self.mic = bool(flag)
        self.touched.add("vbcable")
        if self.statuses.get("vbcable", {}).get("state") != STATE_OK:
            self.selection["vbcable"] = self.mic

    def selected_ids(self):
        ids = [cid for cid, v in self.selection.items() if v and is_selectable(cid, self.statuses)]
        return expand_selection(ids, self.statuses)

    # -- インストール -----------------------------------------------------
    def start(self, ids):
        """ids をインストールする。開始できなければ False。"""
        if self.busy or not ids or not self.can_start():
            return False
        if self.guided is not None and not self.guided.finished:
            return False
        ids = expand_selection(ids, self.statuses)
        auto_ids, guided_ids = split_ids(ids)
        self._pending_guided = list(guided_ids)
        if not auto_ids:
            # 自動で入れるものがない: 手順つきインストールだけを始める (管理者の確認は出さない)
            self._pending_guided = []
            self.audio_message = ""
            self.outcome = {"results": {}, "manual": [], "reboot": False, "error": "", "statuses": {}, "ids": []}
            self.log_lines = []
            self.stage = t("setup.wizard.setupcontroller_start_1")
            self.progress = 0.0
            self.cancel.clear()
            self._notify("start")
            self.guided_begin(guided_ids)
            return True
        ids = auto_ids
        guard = None  # 音声デバイスの保護は VB-CABLE (手順つき) のほうで行う
        self.audio_message = ""
        self.busy = True
        self.outcome = None
        self.log_lines = []
        self.stage = t("setup.wizard.setupcontroller_start_1")
        self.progress = 0.0
        self.cancel.clear()
        self.on_busy(True)
        self._notify("start")

        def emit(kind, **kw):
            self._q.put((kind, kw.get("text") if kind in ("stage", "log") else kw.get("value")))

        def work():
            try:
                out = self.engine.run(ids, emit=emit, cancel=self.cancel)
            except Exception as e:
                out = {"results": {}, "manual": [], "reboot": False, "statuses": {}, "ids": ids,
                       "error": t("setup.wizard.start_work_1") % e}
            if guard is not None:
                self.audio_polling = True  # "done" より先に立てる (UI の更新ループを止めないため)
            self._q.put(("done", out))
            if guard is not None:
                try:
                    res = guard.after_install()
                except Exception:
                    res = {"message": ""}
                self._q.put(("audio", res))

        threading.Thread(target=work, name="setup-run", daemon=True).start()
        self._arm()
        return True

    # -- 手順つきインストール -----------------------------------------------
    def _emit_guided(self, kind, **kw):
        self._q.put((kind, kw.get("text") if kind in ("stage", "log") else kw.get("value")))

    def guided_begin(self, ids):
        """iTunes / iCloud / AltServer / VB-CABLE を、公式インストーラーを開く形で順に進める。"""
        ids = [c for c in ids if c in GUIDED_IDS]
        if not ids:
            return False
        make = self.guided_factory or (lambda i: GuidedFlow(
            i, self.engine.dir, downloader=self.engine.downloader, store_remover=self.engine.store_remover,
            detector=self.detector))
        self.guided = make(ids)
        self._guided_guard = None
        if "vbcable" in ids and self.audio_guard is not None:
            try:
                self.audio_guard.before_install()  # 通常権限のこのプロセスで、現在の既定の音声デバイスを控える
                self._guided_guard = self.audio_guard
            except Exception:
                self._guided_guard = None
        self.cancel.clear()
        self._notify("guided")
        self._guided_run(lambda: None)
        return True

    def _guided_run(self, action):
        """action (check など) を実行してから、次の項目の準備 (ダウンロード) まで、ワーカースレッドで進める。"""
        flow = self.guided
        if flow is None or self.guided_working:
            return False
        self.guided_working = True

        def work():
            try:
                action()
                while flow.phase == GuidedFlow.PREPARING and not flow.finished and not self.closed:
                    flow.prepare(self._emit_guided, self.cancel)
            except Exception as e:
                flow._fail(t("setup.wizard.guided_run_work_1"), str(e))
            finally:
                self.guided_working = False
                self._q.put(("guided", None))

        threading.Thread(target=work, name="setup-guided", daemon=True).start()
        self._arm()
        return True

    def guided_open(self):
        """「インストーラーを開く」。UAC の確認で止まることがあるので、スレッドで開く。"""
        flow = self.guided
        if flow is None or self.guided_working or flow.phase not in (GuidedFlow.READY, GuidedFlow.RUNNING, GuidedFlow.MISSING):
            return False
        self.guided_working = True

        def work():
            try:
                flow.launch()
            except Exception as e:
                flow._fail(t("setup.wizard.guided_open_work_1"), str(e))
            finally:
                self.guided_working = False
                self._q.put(("guided", None))

        threading.Thread(target=work, name="setup-guided-open", daemon=True).start()
        self._arm()
        return True

    def guided_confirm(self):
        """「確認」: 導入済みか確かめる (済みなら次の項目へ)。"""
        flow = self.guided
        if flow is None or flow.phase not in (GuidedFlow.RUNNING, GuidedFlow.MISSING):
            return False
        return self._guided_run(lambda: flow.check(auto=False))

    def guided_retry(self):
        flow = self.guided
        if flow is None:
            return False
        flow.retry()
        self._notify("guided")
        return self._guided_run(lambda: None)

    def guided_skip(self):
        flow = self.guided
        if flow is None or self.guided_working:
            return False
        flow.skip()
        self._notify("guided")
        return self._guided_run(lambda: None) if not flow.finished else self._guided_updated()

    def guided_abort(self):
        flow = self.guided
        if flow is None:
            return False
        self.cancel.set()
        flow.abort()
        self._guided_updated()
        return True

    def guided_tick(self):
        """UI から1秒ごとに呼ぶ。実行中のインストーラーが終わった / 一定時間たったら、裏で導入済みか確かめる。"""
        flow = self.guided
        if flow is None or flow.finished or self.guided_working:
            return False
        try:
            if flow.needs_auto_check():
                return self._guided_run(lambda: flow.check(auto=True))
        except Exception:
            pass
        return False

    def _guided_updated(self):
        flow = self.guided
        if flow is not None and flow.finished and not getattr(flow, "_merged", False):
            flow._merged = True
            out = self.outcome if isinstance(self.outcome, dict) else {"results": {}, "manual": [], "reboot": False,
                                                                       "error": "", "statuses": {}, "ids": []}
            self.outcome = out
            out["ids"] = list(out.get("ids", []))
            out["results"] = dict(out.get("results", {}))
            for cid in flow.ids:
                r = flow.results.get(cid, {"ok": False, "skipped": True, "detail": ""})
                out.setdefault("results", {})[cid] = dict(r, code=None)
                if cid not in out.setdefault("ids", []):
                    out["ids"].append(cid)
            if flow.reboot:
                out["reboot"] = True
            guard = self._guided_guard
            if guard is not None:
                self.audio_polling = True
                self._guided_guard = None

                def after():
                    try:
                        res = guard.after_install()
                    except Exception:
                        res = {"message": ""}
                    self._q.put(("audio", res))

                threading.Thread(target=after, name="setup-audio", daemon=True).start()
                self._arm()
            self._notify("guided")
            self._notify("guided_done")
            self.recheck()
            self.on_changed()
            return True
        self._notify("guided")
        return False

    def close(self):
        self.closed = True
        self.cancel.set()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# 色はテーマ (phonecam.ui.theme.colors()) から取る。取れないとき (単体実行・テスト) は下のライトの値。
_FALLBACK_COLORS = {"bg": "#fafafa", "card": "#ffffff", "text": "#1c1c1c", "text2": "#616161",
                    "separator": "#e5e5e5", "accent": "#005fb8", "live": "#c42b1c", "ok": "#0f7b0f"}

BG = PANEL = FG = SUB = LINE = ACCENT = GOOD = WARN = BAD = ""
ACCENT_FG = ACCENT_HOVER = PANEL_HOVER = DISABLED_FG = ""
_COLOR_NAMES = ("BG", "PANEL", "FG", "SUB", "LINE", "ACCENT", "GOOD", "WARN", "BAD",
                "ACCENT_FG", "ACCENT_HOVER", "PANEL_HOVER", "DISABLED_FG")


def _rgb(c):
    try:
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return 255, 255, 255


def _lum(c):
    r, g, b = _rgb(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _blend(c1, c2, f):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def _theme_colors():
    try:
        from phonecam.ui import theme as _theme
        c = dict(_theme.colors())
        for k, v in _FALLBACK_COLORS.items():
            c.setdefault(k, v)
        return c
    except Exception:
        return dict(_FALLBACK_COLORS)


def _apply_palette():
    """色の定数 (BG など) を現在のテーマの色にする。変更前の値の dict を返す。"""
    g = globals()
    old = {n: g.get(n, "") for n in _COLOR_NAMES}
    c = _theme_colors()
    dark = _lum(c["bg"]) < 0.5
    g["BG"] = c["bg"]
    g["PANEL"] = c["card"]
    g["FG"] = c["text"]
    g["SUB"] = c["text2"]
    g["LINE"] = c["separator"]
    g["ACCENT"] = c["accent"]
    g["GOOD"] = c["ok"]
    g["BAD"] = c["live"]
    g["WARN"] = "#e0a030" if dark else "#b25000"  # 警告のくすんだ琥珀色
    g["ACCENT_FG"] = "#1c1c1c" if _lum(c["accent"]) > 0.6 else "#ffffff"
    g["ACCENT_HOVER"] = _blend(c["accent"], c["text"], 0.12)
    g["PANEL_HOVER"] = _blend(c["card"], c["text"], 0.08)
    g["DISABLED_FG"] = _blend(c["bg"], c["text"], 0.35)
    try:  # 状態アイコンの色表も更新する (定義より前に呼ばれたときは何もしない)
        g["ICONS"][STATE_OK] = ("✓", GOOD, g["ICONS"][STATE_OK][2])
        g["ICONS"][STATE_MISSING] = ("●", SUB, g["ICONS"][STATE_MISSING][2])
        g["ICONS"][STATE_WARN] = ("!", WARN, g["ICONS"][STATE_WARN][2])
        g["DIAG_ICONS"][DIAG_OK] = ("✓", GOOD)
        g["DIAG_ICONS"][DIAG_NG] = ("✗", BAD)
        g["DIAG_ICONS"][DIAG_WARN] = ("!", WARN)
    except Exception:
        pass
    return old


_apply_palette()

FONT = "Yu Gothic UI"

ICONS = {STATE_OK: ("✓", GOOD, t("setup.wizard.icons_1")), STATE_MISSING: ("●", SUB, t("setup.wizard.icons_2")), STATE_WARN: ("!", WARN, t("setup.wizard.icons_3"))}

# 画面の拡大率 (DPI / 96)。フォントは tk の scaling でそろうが、ピクセル指定の余白・幅は px() で拡大する
SCALE = 1.0


def set_scale(f):
    global SCALE
    try:
        SCALE = max(1.0, min(3.0, float(f)))
    except Exception:
        SCALE = 1.0


def px(n):
    return max(1, int(round(n * SCALE)))


def detect_scale(root):
    """tk の実際の DPI から拡大率を求める (取れなければ 1.0)。"""
    try:
        return float(root.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0


_TOPS = []
_COLOR_OPTS = ("bg", "fg", "background", "foreground", "activebackground", "activeforeground", "disabledforeground",
               "readonlybackground", "highlightbackground", "highlightcolor", "selectbackground", "selectforeground",
               "insertbackground")


def _recolor(widget, cmap):
    """widget 以下のウィジェット (tk 版) の色を cmap (旧→新) で置き換える。"""
    if getattr(widget, "_pc_keep", False):
        return
    for opt in _COLOR_OPTS:
        try:
            cur = str(widget.cget(opt)).lower()
        except Exception:
            continue
        if cur in cmap:
            try:
                widget.configure(**{opt: cmap[cur]})
            except Exception:
                pass
    try:
        if widget.winfo_class() == "Canvas":
            for item in widget.find_all():
                for opt in ("fill", "outline"):
                    try:
                        cur = str(widget.itemcget(item, opt)).lower()
                    except Exception:
                        continue
                    if cur in cmap:
                        widget.itemconfigure(item, **{opt: cmap[cur]})
    except Exception:
        pass
    try:
        kids = widget.winfo_children()
    except Exception:
        kids = []
    for k in kids:
        _recolor(k, cmap)


def _on_theme_change():
    old = _apply_palette()
    cmap = {}
    for n in _COLOR_NAMES:
        if old.get(n) and old[n] != globals()[n]:
            cmap[old[n].lower()] = globals()[n]
    if not cmap:
        return
    for top in list(_TOPS):
        try:
            if not top.winfo_exists():
                _TOPS.remove(top)
                continue
            _recolor(top, cmap)
        except Exception:
            try:
                _TOPS.remove(top)
            except ValueError:
                pass


def _track_top(top):
    """テーマ (ライト/ダーク) が切り替わったとき top の色を付け替える。"""
    _TOPS.append(top)
    try:
        from phonecam.ui import theme as _theme
        _theme.on_change(_on_theme_change)
    except Exception:
        pass


def make_button(parent, text, command, primary=False, state="normal", big=False):
    """角のない平らなボタン。big は押す場所をはっきり見せたいときの大きいボタン。"""
    if primary:
        b = tk.Button(parent, text=text, command=command, bg=ACCENT, fg=ACCENT_FG, activebackground=ACCENT_HOVER,
                      activeforeground=ACCENT_FG, relief="flat", bd=0, padx=px(40 if big else 22), pady=px(12 if big else 7),
                      cursor="hand2", font=(FONT, 12 if big else 10, "bold"), disabledforeground=DISABLED_FG, state=state)
    else:
        b = tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG, activebackground=PANEL_HOVER,
                      activeforeground=FG, relief="flat", bd=0, padx=px(16), pady=px(6), cursor="hand2",
                      font=(FONT, 10), disabledforeground=DISABLED_FG, state=state)
    return b


def make_link(parent, text, command, bg=None):
    if bg is None:
        bg = BG
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=SUB, activebackground=bg, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2", font=(FONT, 9), padx=px(4), pady=px(2))
    return b


def bind_wrap(label, container, reserve=0, minimum=140):
    """label の wraplength を container の実際の幅に合わせる (固定ピクセルにしない)。
    reserve はほかの列が使う幅 (数値、または呼ぶと数値を返す関数)。"""
    def fit(_e=None):
        try:
            w = container.winfo_width()
            if w > 1:
                r = reserve() if callable(reserve) else reserve
                label.configure(wraplength=max(px(minimum), int(w - r)))
        except Exception:
            pass
    container.bind("<Configure>", fit, add="+")
    return fit


def wrapped_label(parent, text, container=None, reserve=0, **kw):
    """折り返すラベル。初期の wraplength を持たせ、container の幅に追従させる。"""
    kw.setdefault("bg", BG)
    kw.setdefault("fg", SUB)
    kw.setdefault("font", (FONT, 10))
    kw.setdefault("justify", "left")
    kw.setdefault("anchor", "w")
    lbl = tk.Label(parent, text=text, wraplength=px(440), **kw)
    if container is not None:
        bind_wrap(lbl, container, reserve)
    return lbl


class ThinProgress:
    """細い直線のプログレスバー。"""

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, height=px(4), bg=PANEL, highlightthickness=0, bd=0)
        self.value = 0.0
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def set(self, v):
        self.value = max(0.0, min(1.0, v))
        self._draw()

    def _draw(self):
        try:
            w = self.canvas.winfo_width()
            self.canvas.delete("all")
            self.canvas.create_rectangle(0, 0, int(w * self.value), px(4), fill=ACCENT, width=0)
        except Exception:
            pass


class ScrollFrame:
    """縦にスクロールできる領域 (Canvas + 内側の Frame)。中身が収まるときはスクロールバーを隠す。"""

    def __init__(self, parent, bg=None):
        if bg is None:
            bg = BG
        self.outer = tk.Frame(parent, bg=bg)
        self.canvas = tk.Canvas(self.outer, bg=bg, highlightthickness=0, bd=0, yscrollincrement=px(24))
        self.vsb = tk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar_visible = False
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _e=None):
        try:
            self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        except Exception:
            pass

    def _on_canvas(self, e):
        try:
            self.canvas.itemconfigure(self._win, width=e.width)
        except Exception:
            pass

    def _on_yscroll(self, lo, hi):
        try:
            self.vsb.set(lo, hi)
            need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
            if need and not self.bar_visible:
                self.vsb.pack(side="right", fill="y")
                self.bar_visible = True
            elif not need and self.bar_visible:
                self.vsb.pack_forget()
                self.bar_visible = False
        except Exception:
            pass

    def wheel(self, e):
        try:
            if self.bar_visible:
                self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        except Exception:
            pass

    def to_top(self):
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass


class SetupPanel:
    """コンポーネント一覧。mode = "wizard" (チェックボックス) / "tab" (行ごとのインストールボタン) / "result" (結果表示)。"""

    def __init__(self, parent, controller, mode="wizard", on_install_one=None, results=None):
        self.controller = controller
        self.mode = mode
        self.on_install_one = on_install_one
        self.results = results or {}
        self.frame = tk.Frame(parent, bg=BG)
        self.vars = {}
        self.install_buttons = {}

    def pack(self, **kw):
        self.frame.pack(**kw)

    def set_results(self, results):
        self.results = results or {}

    def render(self):
        for w in list(self.frame.winfo_children()):
            w.destroy()
        self.vars = {}
        self.install_buttons = {}
        c = self.controller
        if not c.checked:
            tk.Label(self.frame, text=t("setup.wizard.setuppanel_render_1"), bg=BG, fg=SUB, font=(FONT, 10)).pack(anchor="w", pady=px(20))
            return
        for comp in COMPONENTS:
            self._row(comp)

    def _row(self, comp):
        c = self.controller
        cid = comp["id"]
        st = c.statuses.get(cid, {"state": STATE_WARN, "detail": ""})
        state = st.get("state", STATE_WARN)
        res = self.results.get(cid)
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x")
        inner = tk.Frame(row, bg=BG)
        inner.pack(fill="x", padx=px(2), pady=px(9))
        # 列: 0 = 状態アイコン (固定) / 1 = 文章 (伸びる) / 2 = チェックやボタン (固定)
        inner.columnconfigure(0, minsize=px(34))
        inner.columnconfigure(1, weight=1)

        if res is not None:
            icon, color, word = ("✓", GOOD, t("setup.wizard.setupengine__run_elevated_7")) if res.get("ok") else ("✗", BAD, t("setup.wizard.setuppanel__row_1"))
        else:
            icon, color, word = ICONS.get(state, ICONS[STATE_WARN])
        icon_lbl = tk.Label(inner, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold"))
        icon_lbl.grid(row=0, column=0, sticky="nw")
        action = tk.Frame(inner, bg=BG)
        action.grid(row=0, column=2, sticky="ne", padx=(px(10), 0))
        text_col = tk.Frame(inner, bg=BG)
        text_col.grid(row=0, column=1, sticky="ew")
        head = tk.Frame(text_col, bg=BG)
        head.pack(anchor="w")
        tk.Label(head, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text="  " + word, fg=color, bg=BG, font=(FONT, 9)).pack(side="left")
        detail = (res or {}).get("detail") if res is not None else st.get("detail", "")

        def reserve():
            return px(34) + action.winfo_reqwidth() + px(24)

        wrapped_label(text_col, comp["purpose"], inner, reserve, font=(FONT, 9)).pack(fill="x")
        if detail:
            wrapped_label(text_col, detail, inner, reserve, font=(FONT, 9),
                          fg=(BAD if res is not None else WARN)).pack(fill="x")
        if cid in GUIDED_IDS and state != STATE_OK and res is None and self.mode in ("wizard", "tab"):
            wrapped_label(text_col, t("setup.wizard.setuppanel__row_2"), inner, reserve, font=(FONT, 9),
                          fg=ACCENT).pack(fill="x")
        if self.mode == "wizard" and res is None:
            self._wizard_action(action, comp, state)
        elif self.mode == "tab" and res is None:
            self._tab_action(action, comp, state)
        tk.Frame(self.frame, bg=LINE, height=1).pack(fill="x")

    def _wizard_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK:
            tk.Label(action, text=t("setup.wizard.setuppanel__wizard_action_1"), fg=SUB, bg=BG, font=(FONT, 9)).pack()
            return
        if not is_selectable(cid, c.statuses):
            return
        var = tk.BooleanVar(value=bool(c.selection.get(cid)))
        self.vars[cid] = var
        locked = comp["required"] and state == STATE_MISSING
        if locked:
            var.set(True)
        cb = tk.Checkbutton(action, variable=var, bg=BG, activebackground=BG, bd=0, highlightthickness=0,
                            command=lambda cid=cid, v=var: c.set_selected(cid, v.get()))
        if locked:
            cb.config(state="disabled")
        cb.pack()

    def _tab_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK or not is_selectable(cid, c.statuses):
            return
        label = t("setup.wizard.setuppanel__tab_action_1") if cid == "camera" and c.statuses.get(cid, {}).get("detail") else t("setup.wizard.setuppanel__tab_action_2")
        b = make_button(action, label, lambda cid=cid: self.on_install_one and self.on_install_one(cid),
                        state=("disabled" if c.busy else "normal"))
        b.pack()
        self.install_buttons[cid] = b


def find_itunes():
    return _any_file([os.path.join("iTunes", "iTunes.exe")])


def text_section(parent, title, lines, container, numbered=False, intro=""):
    """見出し + 箇条書き (または番号つき) の文章ブロック。"""
    tk.Label(parent, text=title, bg=BG, fg=FG, font=(FONT, 11, "bold"), anchor="w").pack(anchor="w", pady=(px(14), 0))
    if intro:
        wrapped_label(parent, intro, container, font=(FONT, 9)).pack(fill="x", pady=(px(2), 0))
    for n, line in enumerate(lines, 1):
        mark = ("%d.  " % n) if numbered else t("setup.wizard.detect_itunes_1")
        wrapped_label(parent, mark + line, container, fg=FG, font=(FONT, 9)).pack(fill="x", pady=(px(3), 0))


DIAG_ICONS = {DIAG_OK: ("✓", GOOD), DIAG_NG: ("✗", BAD), DIAG_WARN: ("!", WARN)}


class DiagnosticsDialog:
    """「AltServerの診断」。チェックごとに ✓/✗ と、直し方を1行で出す。診断は別スレッドで実行する。"""

    def __init__(self, root, runner=None, open_url=None):
        self.runner = runner or run_diagnostics
        self.open_url = open_url or webbrowser.open
        self.root = root
        self.results = None
        self.running = False
        self.progress_text = ""
        self._box = []
        self._job = None
        self.closed = False
        set_scale(detect_scale(root))
        _apply_palette()
        self.top = tk.Toplevel(root)
        _track_top(self.top)
        self.top.title(t("setup.wizard.diagnosticsdialog___init___1"))
        self.top.configure(bg=BG)
        try:
            self.top.minsize(px(520), px(420))
            self.top.protocol("WM_DELETE_WINDOW", self.close)
        except Exception:
            pass
        foot = tk.Frame(self.top, bg=BG)
        foot.pack(side="bottom", fill="x", padx=px(24), pady=px(12))
        make_button(foot, t("setup.wizard.diagnosticsdialog___init___2"), self.close).pack(side="right")
        self.run_btn = make_button(foot, t("setup.wizard.diagnosticsdialog___init___3"), self.run, primary=True)
        self.run_btn.pack(side="right", padx=(0, px(10)))
        self.scroll = ScrollFrame(self.top)
        self.scroll.outer.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(24), pady=px(18))
        try:
            self.top.bind("<MouseWheel>", self.scroll.wheel)
        except Exception:
            pass
        self.render()
        self.run()

    def run(self):
        if self.running or self.closed:
            return
        self.running = True
        self.results = None
        self.progress_text = t("setup.wizard.diagnosticsdialog_run_1")
        self._box = []
        try:
            self.run_btn.config(state="disabled")
        except Exception:
            pass
        self.render()

        def prog(msg):
            if msg:
                self.progress_text = msg

        def work():
            try:
                self._box.append(self.runner(progress=prog))
            except Exception as e:
                self._box.append([_diag("error", t("setup.wizard.run_work_1"), DIAG_WARN, t("setup.wizard.run_work_2") % e, t("setup.wizard.run_work_3"))])

        threading.Thread(target=work, name="altserver-diag", daemon=True).start()
        self._schedule()

    def _schedule(self):
        try:
            self._job = self.top.after(300, self._poll)
        except Exception:
            self._job = None

    def _poll(self):
        self._job = None
        if self.closed:
            return
        if self._box:
            self.results = self._box[0]
            self.running = False
            try:
                self.run_btn.config(state="normal")
            except Exception:
                pass
            self.render()
            return
        if self.status_label is not None:
            try:
                self.status_label.config(text=self.progress_text)
            except Exception:
                pass
        self._schedule()

    def render(self):
        for w in list(self.body.winfo_children()):
            w.destroy()
        self.status_label = None
        tk.Label(self.body, text=t("setup.wizard.diagnosticsdialog___init___1"), bg=BG, fg=FG, font=(FONT, 18, "bold"), anchor="w").pack(anchor="w")
        if self.running or self.results is None:
            self.status_label = wrapped_label(self.body, self.progress_text or t("setup.wizard.diagnosticsdialog_run_1"), self.body, fg=SUB)
            self.status_label.pack(fill="x", pady=(px(8), 0))
        else:
            wrapped_label(self.body, diagnostics_summary(self.results), self.body, fg=FG, font=(FONT, 10)).pack(
                fill="x", pady=(px(6), px(8)))
            for r in self.results:
                icon, color = DIAG_ICONS.get(r["state"], DIAG_ICONS[DIAG_WARN])
                row = tk.Frame(self.body, bg=BG)
                row.pack(fill="x", pady=px(5))
                row.columnconfigure(1, weight=1)
                tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold")).grid(row=0, column=0, sticky="nw",
                                                                                      padx=(0, px(10)))
                col = tk.Frame(row, bg=BG)
                col.grid(row=0, column=1, sticky="ew")
                tk.Label(col, text=r["label"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
                if r.get("detail"):
                    wrapped_label(col, r["detail"], row, px(40), fg=color, font=(FONT, 9)).pack(fill="x")
                if r.get("fix") and r["state"] != DIAG_OK:
                    wrapped_label(col, "→ " + r["fix"], row, px(40), fg=FG, font=(FONT, 9)).pack(fill="x")
        if iphone is not None:
            text_section(self.body, iphone.ALT_2003_TITLE, iphone.ALT_2003_CHECKLIST, self.body, numbered=True)
            text_section(self.body, t("setup.wizard.diagnosticsdialog_render_1"), iphone.ALTSTORE_REQUIREMENTS, self.body, numbered=True,
                         intro=t("setup.wizard.diagnosticsdialog_render_2"))
            wrapped_label(self.body, iphone.ALTSTORE_NEW_PC_NOTE, self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
        make_link(self.body, t("setup.wizard.diagnosticsdialog_render_3"), lambda: self.open_url(FAQ_URL)).pack(anchor="w", pady=(px(10), 0))
        try:
            self.top.update_idletasks()
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            w = min(max(self.top.winfo_width(), px(620)), int(sw * 0.9))
            h = min(max(self.top.winfo_height(), px(560)), int(sh * 0.9))
            self.top.geometry("%dx%d" % (w, h))
        except Exception:
            pass

    def close(self):
        self.closed = True
        if self._job is not None:
            try:
                self.top.after_cancel(self._job)
            except Exception:
                pass
        try:
            self.top.destroy()
        except Exception:
            pass


def open_diagnostics(root, open_url=None, runner=None):
    """「AltServerの診断」ウィンドウを開く。開けなかったら None。"""
    try:
        return DiagnosticsDialog(root, runner=runner, open_url=open_url)
    except Exception:
        return None


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else tempfile.gettempdir()


def _open_folder_select(path):
    """エクスプローラーでファイルの場所を開く (失敗したらフォルダだけ開く)。"""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
    except Exception:
        pass
    _startfile(os.path.dirname(path))


# 画面ページの番号: 0 ようこそ / 1 確認 / 2 インストール / 3〜9 iPhone の手順 (7つ)
PAGE_WELCOME, PAGE_CHECK, PAGE_INSTALL, PAGE_IPHONE = 0, 1, 2, 3
POLL_MS = 2000


class SetupWizard:
    """初回セットアップ / やり直し用のウィザード (リサイズ可能な Toplevel)。
    start="iphone" のときは iPhone の手順だけを開く (セットアップタブの「iPhoneのセットアップ」)。"""

    TITLES = [t("setup.wizard.setupwizard_1"), t("setup.wizard.setupwizard_2"), t("setup.wizard.setuppanel__tab_action_2"), t("setup.wizard.setupwizard_3")]

    def __init__(self, root, controller, on_finish, on_skip=None, open_url=None, launch=None, start="setup"):
        self.root = root
        self.controller = controller
        self.on_finish = on_finish
        self.on_skip = on_skip or on_finish
        self.open_url = open_url or webbrowser.open
        self.launch = launch or _startfile
        self.mode = start
        self.page = 0
        self.finished = False
        self._placed = False
        set_scale(detect_scale(root))
        _apply_palette()
        self.top = tk.Toplevel(root)
        _track_top(self.top)
        self.top.title(t("setup.wizard.setupwizard___init___1"))
        self.top.configure(bg=BG)
        try:
            self.top.minsize(px(520), px(440))
            self.top.resizable(True, True)
            self.top.transient(root)
            self.top.protocol("WM_DELETE_WINDOW", self._close)
        except Exception:
            pass
        # フッター (ボタン) は一番下に固定。本文だけがスクロールする
        self.footer_wrap = tk.Frame(self.top, bg=BG)
        self.footer_wrap.pack(side="bottom", fill="x")
        tk.Frame(self.footer_wrap, bg=LINE, height=1).pack(fill="x")
        self.footer = tk.Frame(self.footer_wrap, bg=BG)
        self.footer.pack(fill="x", padx=px(28), pady=px(14))
        self.scroll = ScrollFrame(self.top)
        self.scroll.outer.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(28), pady=(px(24), px(10)))
        try:
            self.top.bind("<MouseWheel>", self.scroll.wheel)
        except Exception:
            pass
        self.panel = None
        self.log_visible = False
        self.log_text = None
        self._listener = self._on_controller
        controller.listen(self._listener)
        # iPhone ページの状態
        self.snap = {}
        self.app_ok = False
        self._visited = set()
        self._first = False
        self._probing = False
        self._probe_result = None
        self._next_probe = 0.0
        self._pump_job = None
        self._dl_state = None
        self.show(PAGE_IPHONE if start == "iphone" else (PAGE_INSTALL if start == "guided" else PAGE_WELCOME))
        try:
            self.top.grab_set()
        except Exception:
            pass

    # -- 共通 -------------------------------------------------------------
    def _clear(self):
        for w in list(self.body.winfo_children()):
            w.destroy()
        for w in list(self.footer.winfo_children()):
            w.destroy()
        self.panel = None
        self.log_text = None
        self.stage_label = None
        self.bar = None
        self.next_btn = None
        self.retry_btn = None
        self.result_holder = None
        self.note_label = None
        self.audio_label = None
        self.chip = None
        self.dl_label = None
        self.dl_btn = None
        self.guided_holder = None
        self.manual_holder = None
        self.abort_btn = None
        self.g_elapsed = None
        self.g_hint = None
        self._g_job = None

    def _title(self, text, sub=None):
        tk.Label(self.body, text=text, bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w").pack(anchor="w")
        if sub:
            wrapped_label(self.body, sub, self.body).pack(fill="x", pady=(px(6), px(14)))

    def show(self, index):
        self.page = index
        self._clear()
        if index >= PAGE_IPHONE:
            self._page_step(index - PAGE_IPHONE)
        else:
            [self._page_welcome, self._page_check, self._page_install][index]()
        self.scroll.to_top()
        self._fit_window()
        if index >= PAGE_IPHONE:
            self._start_pump()

    def _fit_window(self):
        """中身の大きさに合わせてウィンドウを大きくする (画面の85%まで。小さくはしない)。"""
        try:
            self.top.update_idletasks()
            pad_w = 2 * px(28) + px(24)
            need_w = max(self.body.winfo_reqwidth() + pad_w, self.footer_wrap.winfo_reqwidth(), px(560))
            need_h = (self.body.winfo_reqheight() + px(24) + px(10) + self.footer_wrap.winfo_reqheight())
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            cur_w, cur_h = self.top.winfo_width(), self.top.winfo_height()
            if not self._placed:
                cur_w = cur_h = 1
            w = min(max(cur_w, need_w), int(sw * 0.85))
            h = min(max(cur_h, need_h), int(sh * 0.85))
            if not self._placed:
                try:
                    rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                    rw, rh = self.root.winfo_width(), self.root.winfo_height()
                except Exception:
                    rx = ry = 0
                    rw, rh = sw, sh
                x = max(0, min(sw - w, rx + (rw - w) // 2))
                y = max(0, min(sh - h - px(40), ry + max(0, (rh - h) // 3)))
                self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
                self._placed = True
            elif (w, h) != (cur_w, cur_h):
                self.top.geometry("%dx%d" % (w, h))
        except Exception:
            pass

    # -- ページ1 ----------------------------------------------------------
    def _page_welcome(self):
        tk.Label(self.body, text=t("setup.wizard.setupwizard__page_welcome_1"), bg=BG, fg=FG, font=(FONT, 22, "bold"), anchor="w").pack(
            anchor="w", pady=(px(36), 0))
        wrapped_label(self.body, t("setup.wizard.setupwizard__page_welcome_2"),
                      self.body, font=(FONT, 11)).pack(fill="x", pady=(px(16), 0))
        make_link(self.footer, t("setup.wizard.setuppanel__wizard_action_1"), self._skip).pack(side="left")
        make_button(self.footer, t("setup.wizard.setupwizard__page_welcome_3"), lambda: self.show(PAGE_CHECK), primary=True).pack(side="right")

    # -- ページ2 ----------------------------------------------------------
    def _page_check(self):
        self._title(t("setup.wizard.setupwizard_2"))
        self.panel = SetupPanel(self.body, self.controller, "wizard")
        self.panel.pack(fill="x")
        self.mic_var = tk.BooleanVar(value=self.controller.mic)
        # VB-CABLE が導入済みなら選ぶ必要がないので表示しない (_sync_mic_option で出し入れする)
        self.mic_box = tk.Frame(self.body, bg=BG)
        tk.Checkbutton(self.mic_box, text=t("setup.wizard.setupwizard__page_check_1"), variable=self.mic_var, bg=BG,
                       activebackground=BG, fg=FG, font=(FONT, 10), bd=0, highlightthickness=0,
                       command=self._mic_changed).pack(anchor="w")
        tk.Label(self.mic_box, text=t("setup.wizard.setupwizard__page_check_2"),
                 bg=BG, fg=SUB, font=(FONT, 9), justify="left", anchor="w",
                 wraplength=px(480)).pack(anchor="w", fill="x", padx=(px(22), 0))
        self._sync_mic_option()
        make_link(self.footer, t("setup.wizard.setuppanel__wizard_action_1"), self._skip).pack(side="left")
        self.next_btn = make_button(self.footer, t("setup.wizard.setuppanel__tab_action_2"), self._start_install, primary=True)
        self.next_btn.pack(side="right")
        make_button(self.footer, t("setup.wizard.setupwizard__page_check_3"), self.controller.recheck).pack(side="right", padx=(0, px(10)))
        self.panel.render()
        self._update_next_label()
        if not self.controller.checked:
            self.controller.recheck()

    def _sync_mic_option(self):
        box = getattr(self, "mic_box", None)
        if box is None:
            return
        try:
            installed = self.controller.statuses.get("vbcable", {}).get("state") == STATE_OK
            if installed:
                box.pack_forget()
            elif not box.winfo_ismapped():
                box.pack(anchor="w", fill="x", pady=(px(12), 0))
        except Exception:
            pass

    def _mic_changed(self):
        self.controller.set_mic(self.mic_var.get())
        self.panel.render()
        self._update_next_label()
        self._fit_window()

    def _update_next_label(self):
        if self.next_btn is None:
            return
        ids = self.controller.selected_ids()
        try:
            self.next_btn.config(text=t("setup.wizard.setuppanel__tab_action_2") if ids else t("setup.wizard.setupwizard__page_welcome_3"))
        except Exception:
            pass

    def _start_install(self):
        ids = self.controller.selected_ids()
        if not ids:
            self.show(PAGE_IPHONE)
            return
        if self.controller.start(ids):
            self.show(PAGE_INSTALL)

    # -- ページ3 ----------------------------------------------------------
    def _page_install(self):
        self._title(t("setup.wizard.setuppanel__tab_action_2"))
        self.stage_label = wrapped_label(self.body, self.controller.stage, self.body, fg=FG)
        self.stage_label.pack(fill="x")
        self.bar = ThinProgress(self.body)
        self.bar.pack(fill="x", pady=(px(8), px(6)))
        self.bar.set(self.controller.progress)
        self.detail_btn = make_link(self.body, t("setup.wizard.setupwizard__page_install_1"), self._toggle_log)
        self.detail_btn.pack(anchor="w")
        self.log_frame = tk.Frame(self.body, bg=BG)
        self.log_text = tk.Text(self.log_frame, height=7, bg=PANEL, fg=SUB, relief="flat", bd=0, font=(FONT, 8),
                                wrap="word", highlightthickness=0, padx=px(8), pady=px(6))
        sb = tk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_visible = False
        self._refill_log()
        self.result_holder = tk.Frame(self.body, bg=BG)
        self.manual_holder = tk.Frame(self.body, bg=BG)
        self.note_label = wrapped_label(self.body, "", self.body, fg=WARN)
        self.guided_holder = tk.Frame(self.body, bg=BG)
        self.guided_holder.pack(fill="x", pady=(px(10), 0))
        self.audio_label = wrapped_label(self.body, "", self.body, fg=SUB, font=(FONT, 9))
        self.g_elapsed = self.g_hint = None
        last_label = t("setup.wizard.diagnosticsdialog___init___2") if self.mode == "guided" else t("setup.wizard.setupwizard__page_welcome_3")
        self.next_btn = make_button(self.footer, last_label, self._after_install, primary=True, state="disabled")
        self.next_btn.pack(side="right")
        self.retry_btn = make_button(self.footer, t("setup.wizard.setupwizard__page_install_2"), self._retry)
        self.abort_btn = make_link(self.footer, t("setup.wizard.setupwizard__page_install_3"), self._abort_guided)
        if not self.controller.busy and self.controller.outcome is not None:
            self._show_outcome()
        self._render_guided()
        self._show_audio_note()
        self._update_install_footer()
        self._schedule_guided_timer()

    def _after_install(self):
        if self.mode == "guided":
            self._end(self.on_finish)
        else:
            self.show(PAGE_IPHONE)

    def _guided_active(self):
        g = self.controller.guided
        return g is not None and not g.finished

    def _install_complete(self):
        c = self.controller
        return (not c.busy) and (not self._guided_active()) and c.outcome is not None

    def _update_install_footer(self):
        try:
            if self.next_btn is not None:
                self.next_btn.config(state="normal" if self._install_complete() else "disabled")
            ab = getattr(self, "abort_btn", None)
            if ab is not None:
                if self._guided_active():
                    ab.pack(side="left")
                else:
                    ab.pack_forget()
        except Exception:
            pass

    def _abort_guided(self):
        self.controller.guided_abort()
        self._show_outcome()

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(px(4), 0))
            self.detail_btn.config(text=t("setup.wizard.setupwizard__toggle_log_1"))
        else:
            self.log_frame.pack_forget()
            self.detail_btn.config(text=t("setup.wizard.setupwizard__page_install_1"))
        self._fit_window()

    def _refill_log(self):
        if self.log_text is None:
            return
        try:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", "\n".join(self.controller.log_lines))
            self.log_text.config(state="disabled")
            self.log_text.see("end")
        except Exception:
            pass

    def _retry(self):
        self.controller.recheck()
        self.show(PAGE_CHECK)

    def _show_audio_note(self):
        """VB-CABLE を入れたあとの、音の出力先に関する案内 (戻した / 戻せなかった)。"""
        msg = getattr(self.controller, "audio_message", "")
        if self.audio_label is None or not msg:
            return
        try:
            self.audio_label.config(text=msg)
            self.audio_label.pack(fill="x", pady=(px(8), 0))
            self._fit_window()
        except Exception:
            pass

    @staticmethod
    def overall_text(out, guided_pending=False):
        res = (out or {}).get("results", {})
        if guided_pending:
            return t("setup.wizard.setupwizard_overall_text_1")
        failed = [r for r in res.values() if not r.get("ok") and not r.get("skipped")]
        skipped = [r for r in res.values() if r.get("skipped")]
        if (out or {}).get("error") or failed:
            return t("setup.wizard.setupwizard_overall_text_2")
        if skipped:
            return t("setup.wizard.setupwizard_overall_text_3")
        return t("setup.wizard.setupwizard_overall_text_4")

    def _show_outcome(self):
        out = self.controller.outcome or {}
        pending = self._guided_active() or bool(getattr(self.controller, "_pending_guided", []))
        if self.stage_label is not None:
            self.stage_label.config(text=self.overall_text(out, pending))
        if self.bar is not None and not pending:
            self.bar.set(1.0)
        self._render_results(out)
        self.result_holder.pack(fill="x", pady=(px(8), 0), before=self.guided_holder)
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        if out.get("reboot"):
            notes.append(t("setup.wizard.setupwizard__show_outcome_1"))
        for m in out.get("manual", []):
            notes.append(t("setup.wizard.setupwizard__show_outcome_2") % (m["name"], m["text"]))
        for w in list(self.manual_holder.winfo_children()):
            w.destroy()
        if notes:
            self.note_label.config(text="\n".join(notes))
            self.note_label.pack(fill="x", pady=(px(8), 0), before=self.guided_holder)
        else:
            try:
                self.note_label.pack_forget()
            except Exception:
                pass
        self.manual_holder.pack(fill="x", before=self.guided_holder)
        for m in out.get("manual", []):
            url = m["url"]
            make_link(self.manual_holder, t("setup.wizard.setupwizard__show_outcome_3") % m["name"], lambda u=url: self.open_url(u)).pack(anchor="w")
        failed = [cid for cid, r in out.get("results", {}).items() if not r.get("ok") and not r.get("skipped")
                  and cid not in GUIDED_IDS]
        if failed and self.retry_btn is not None and not pending:
            self.retry_btn.pack(side="right", padx=(0, px(10)))
        self._update_install_footer()
        self._fit_window()

    def _render_results(self, out):
        """結果の行 (インストールした項目だけ) を描画する。"""
        for w in list(self.result_holder.winfo_children()):
            w.destroy()
        ids = out.get("ids", [])
        for cid in ids:
            comp = COMPONENT_BY_ID[cid]
            r = out["results"].get(cid, {"ok": False, "detail": ""})
            row = tk.Frame(self.result_holder, bg=BG)
            row.pack(fill="x", pady=px(3))
            row.columnconfigure(1, weight=1)
            if r.get("ok"):
                icon, color = "✓", GOOD
            elif r.get("skipped"):
                icon, color = "○", SUB
            else:
                icon, color = "✗", BAD
            tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="nw",
                                                                                   padx=(0, px(10)))
            col = tk.Frame(row, bg=BG)
            col.grid(row=0, column=1, sticky="ew")
            tk.Label(col, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
            if r.get("detail"):
                wrapped_label(col, r["detail"], row, px(40), fg=(BAD if not (r.get("ok") or r.get("skipped")) else WARN),
                              font=(FONT, 9)).pack(fill="x")
            tech = r.get("technical") or ((t("setup.wizard.run_installer_2") % r["code"]) if r.get("code") not in (None, 0) and not r.get("ok") else "")
            if tech:
                wrapped_label(col, t("setup.wizard.setupwizard__render_results_1") % tech, row, px(40), fg=SUB, font=(FONT, 8)).pack(fill="x")

    # -- 手順つきインストール (iTunes / iCloud / AltServer / VB-CABLE) ----------
    def _render_guided(self):
        holder = getattr(self, "guided_holder", None)
        if holder is None:
            return
        for w in list(holder.winfo_children()):
            w.destroy()
        self.g_elapsed = self.g_hint = None
        flow = self.controller.guided
        if flow is None:
            return
        c = self.controller
        if flow.finished:
            self._update_install_footer()
            return
        cid = flow.current
        g = GUIDE.get(cid, {"steps": [], "note": ""})
        tk.Frame(holder, bg=LINE, height=1).pack(fill="x", pady=(0, px(10)))
        tk.Label(holder, text=t("setup.wizard.setupwizard__render_guided_1") % (flow.name(cid), flow.index + 1, len(flow.ids)), bg=BG, fg=FG,
                 font=(FONT, 14, "bold"), anchor="w").pack(anchor="w")
        phase = flow.phase
        if phase == GuidedFlow.PREPARING or phase == GuidedFlow.CHECKING:
            text = t("setup.wizard.setupwizard__render_guided_2") if phase == GuidedFlow.PREPARING else t("setup.wizard.setupwizard__render_guided_3")
            wrapped_label(holder, text, self.body, fg=SUB).pack(fill="x", pady=(px(8), 0))
        else:
            if phase == GuidedFlow.FAILED:
                wrapped_label(holder, flow.message, self.body, fg=BAD).pack(fill="x", pady=(px(8), 0))
                if flow.technical:
                    wrapped_label(holder, t("setup.wizard.setupwizard__render_results_1") % flow.technical, self.body, fg=SUB, font=(FONT, 8)).pack(fill="x")
            else:
                for n, line in enumerate(g["steps"], 1):
                    wrapped_label(holder, "%d.  %s" % (n, line), self.body, fg=FG, font=(FONT, 10)).pack(fill="x", pady=(px(6), 0))
                if g.get("note"):
                    wrapped_label(holder, g["note"], self.body, fg=SUB, font=(FONT, 9)).pack(fill="x", pady=(px(8), 0))
                if flow.notice:
                    wrapped_label(holder, flow.notice, self.body, fg=WARN, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
                if phase == GuidedFlow.READY and flow.message:
                    wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(8), 0))
                if phase == GuidedFlow.RUNNING:
                    self.g_elapsed = wrapped_label(holder, self._elapsed_text(flow), self.body, fg=ACCENT)
                    self.g_elapsed.pack(fill="x", pady=(px(10), 0))
                    if flow.message:
                        wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(4), 0))
                    if flow.detected:
                        wrapped_label(holder, t("setup.wizard.setupwizard__render_guided_4"), self.body, fg=GOOD).pack(fill="x", pady=(px(4), 0))
                    self.g_hint = wrapped_label(holder, SLOW_HINT, self.body, fg=WARN)
                    if flow.is_slow():
                        self.g_hint.pack(fill="x", pady=(px(4), 0))
                if phase == GuidedFlow.MISSING:
                    wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(10), 0))
            row = tk.Frame(holder, bg=BG)
            row.pack(anchor="w", pady=(px(14), 0))
            if phase == GuidedFlow.READY:
                make_button(row, t("setup.wizard.setupwizard__render_guided_5"), c.guided_open, primary=True, big=True).pack(side="left")
            elif phase == GuidedFlow.RUNNING:
                make_button(row, t("setup.wizard.setupwizard__render_guided_6"), c.guided_confirm, primary=True, big=True).pack(side="left")
                make_button(row, t("setup.wizard.setupwizard__render_guided_7"), c.guided_open).pack(side="left", padx=(px(10), 0))
            elif phase == GuidedFlow.MISSING:
                make_button(row, t("setup.wizard.setupwizard__render_guided_6"), c.guided_confirm, primary=True, big=True).pack(side="left")
                make_button(row, t("setup.wizard.setupwizard__render_guided_7"), c.guided_open).pack(side="left", padx=(px(10), 0))
            elif phase == GuidedFlow.FAILED:
                make_button(row, t("setup.wizard.setupwizard__page_install_2"), c.guided_retry, primary=True, big=True).pack(side="left")
            url = flow.page_url(cid)
            row2 = tk.Frame(holder, bg=BG)
            row2.pack(anchor="w", pady=(px(8), 0))
            if url:
                make_link(row2, t("setup.wizard.setupwizard__render_guided_8"), lambda u=url: self.open_url(u)).pack(side="left")
            make_link(row2, t("setup.wizard.setupwizard__render_guided_9"), c.guided_skip).pack(side="left", padx=(px(10), 0))
        self._update_install_footer()
        self._fit_window()

    @staticmethod
    def _elapsed_text(flow):
        sec = int(flow.elapsed())
        return t("setup.wizard.setupwizard__elapsed_text_1") % (sec // 60, sec % 60)

    def _schedule_guided_timer(self):
        if getattr(self, "_g_job", None) is None:
            try:
                self._g_job = self.top.after(1000, self._guided_timer)
            except Exception:
                self._g_job = None

    def _guided_timer(self):
        self._g_job = None
        if self.finished or self.page != PAGE_INSTALL:
            return
        flow = self.controller.guided
        if flow is not None and not flow.finished:
            try:
                self.controller.guided_tick()
                if self.g_elapsed is not None and flow.phase == GuidedFlow.RUNNING:
                    self.g_elapsed.config(text=self._elapsed_text(flow))
                    if self.g_hint is not None and flow.is_slow():
                        self.g_hint.pack(fill="x", pady=(px(4), 0))
            except Exception:
                pass
        self._schedule_guided_timer()

    # -- ページ4〜10: iPhone の手順 ----------------------------------------
    def _ctx(self):
        alt = bool(find_altserver())
        if not alt:
            try:
                alt = self.controller.statuses.get("altserver", {}).get("state") == STATE_OK
            except Exception:
                pass
        link = False
        fn = _host.get("link_connected")
        if fn:
            try:
                link = bool(fn())
            except Exception:
                link = False
        return {"altserver": alt, "app": self.app_ok, "link": link}

    def _page_step(self, i):
        steps = iphone.IPHONE_STEPS
        step = steps[i]
        sid = step["id"]
        self._step_id = sid
        self._step_index = i
        self._first = sid not in self._visited
        self._visited.add(sid)
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=str(i + 1), bg=BG, fg=ACCENT, font=(FONT, 40, "bold")).pack(side="left")
        tk.Label(head, text=t("setup.wizard.setupwizard__page_step_1") % len(steps), bg=BG, fg=SUB, font=(FONT, 9)).pack(side="right", anchor="s")
        tk.Label(self.body, text=step["title"], bg=BG, fg=FG, font=(FONT, 18, "bold"), anchor="w").pack(anchor="w")
        for line in step["lines"]:
            wrapped_label(self.body, line, self.body, font=(FONT, 10)).pack(fill="x", pady=(px(6), 0))
        self.chip = tk.Label(self.body, text=t("setup.wizard.setuppanel_render_1"), bg=PANEL, fg=SUB, font=(FONT, 10), padx=px(12),
                             pady=px(5), anchor="w")
        self.chip.pack(anchor="w", pady=(px(16), px(12)))
        extra = getattr(self, "_extra_" + sid, None)
        if extra:
            extra()
        # フッター
        make_link(self.footer, t("setup.wizard.setupwizard__page_step_2"), self._skip_iphone).pack(side="left")
        last = i == len(steps) - 1
        if last:
            self.next_btn = make_button(self.footer, t("setup.wizard.setupengine__run_elevated_7"), self._finish, primary=True, state="disabled")
        else:
            self.next_btn = make_button(self.footer, t("setup.wizard.setupwizard__page_welcome_3"), lambda: self.show(self.page + 1), primary=True)
        self.next_btn.pack(side="right")
        if not (i == 0 and self.mode == "iphone"):
            make_button(self.footer, t("setup.wizard.setupwizard__page_step_3"), self._back).pack(side="right", padx=(0, px(10)))
        self._render_chip()

    def _back(self):
        if self.page == PAGE_IPHONE:
            self.controller.recheck()
            self.show(PAGE_CHECK)
        else:
            self.show(self.page - 1)

    def _btn_row(self, items):
        """ボタンを2つずつ並べる (幅が足りなくても切れない)。items = [(text, command, enabled)]"""
        row = None
        for n, (text, cmd, enabled) in enumerate(items):
            if n % 2 == 0:
                row = tk.Frame(self.body, bg=BG)
                row.pack(anchor="w", pady=(0, px(8)))
            b = make_button(row, text, cmd, state="normal" if enabled else "disabled")
            b.pack(side="left", padx=(0, px(10)))

    def _extra_itunes(self):
        path = find_itunes()
        self._btn_row([(t("setup.wizard.setupwizard__extra_itunes_1"), lambda: self.launch(path), bool(path))])
        if not path:
            wrapped_label(self.body, t("setup.wizard.setupwizard__extra_itunes_2"), self.body,
                          font=(FONT, 9)).pack(fill="x")

    def _extra_altstore(self):
        path = find_altserver()
        self._btn_row([(t("setup.wizard.setupwizard__extra_altstore_1"), lambda: self.launch(path), bool(path)),
                       (t("setup.wizard.setupwizard__extra_altstore_2"), lambda: self.open_url(FAQ_URL), True),
                       (t("setup.wizard.diagnosticsdialog___init___1"), lambda: open_diagnostics(self.top, self.open_url), True)])
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_altstore_3"),
                      self.body, font=(FONT, 9)).pack(fill="x")
        if iphone is not None:
            text_section(self.body, t("setup.wizard.diagnosticsdialog_render_1"), iphone.ALTSTORE_REQUIREMENTS, self.body, numbered=True,
                         intro=t("setup.wizard.diagnosticsdialog_render_2"))
            wrapped_label(self.body, iphone.ALTSTORE_NEW_PC_NOTE, self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
            text_section(self.body, iphone.ALT_2003_TITLE, iphone.ALT_2003_CHECKLIST, self.body, numbered=True)

    def _extra_phonecam(self):
        repo = APP_REPO
        url = iphone.source_url(repo)
        deeplink = iphone.source_deeplink(repo)
        if url and phonecam_qr is not None:
            box = tk.Frame(self.body, bg=BG)
            box.pack(anchor="w", pady=(0, px(8)))
            cv = tk.Canvas(box, highlightthickness=0, bd=0, bg="#ffffff")  # QRコードは明るい地が必須なのでテーマに依らず白
            cv._pc_keep = True
            try:
                phonecam_qr.draw_on_canvas(cv, phonecam_qr.make_qr(deeplink, "M"), cell=px(4), quiet=3)
                cv.pack(side="left")
            except Exception:
                cv.destroy()
            side = tk.Frame(box, bg=BG)
            side.pack(side="left", padx=(px(16), 0), anchor="n")
            wrapped_label(side, t("setup.wizard.setupwizard__extra_phonecam_1"), self.body, px(260),
                          font=(FONT, 9)).pack(fill="x")
            wrapped_label(side, t("setup.wizard.setupwizard__extra_phonecam_2"), self.body, px(260),
                          font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
            ent = tk.Entry(self.body, font=(FONT, 9), relief="flat", bg=PANEL, fg=FG, readonlybackground=PANEL,
                           highlightthickness=0, bd=0)
            ent.insert(0, url)
            ent.configure(state="readonly")
            ent.pack(fill="x", ipady=px(6), pady=(0, px(8)))
            self._btn_row([(t("setup.wizard.setupwizard__extra_phonecam_3"), lambda: self._copy(url), True)])
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_phonecam_4"),
                      self.body, font=(FONT, 9)).pack(fill="x")
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_phonecam_5"),
                      self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
        ipa = ipa_url()
        self.dl_btn = make_button(self.body, t("setup.wizard.setupwizard__extra_phonecam_6"), self._download_ipa,
                                  state="normal" if ipa else "disabled")
        self.dl_btn.pack(anchor="w", pady=(px(8), 0))
        self.dl_label = wrapped_label(self.body, "", self.body, font=(FONT, 9))
        self.dl_label.pack(fill="x", pady=(px(4), 0))
        if self._dl_state and self._dl_state[0] == "ok":
            self.dl_label.config(text=t("setup.wizard.setupwizard__extra_phonecam_7") % self._dl_state[1])

    def _copy(self, text):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            if self.chip is not None:
                self.chip.config(text=t("setup.wizard.setupwizard__copy_1"), fg=GOOD)
        except Exception:
            pass

    def _download_ipa(self):
        url = ipa_url()
        if not url or (self._dl_state and self._dl_state[0] == "busy"):
            return
        self._dl_state = ("busy", "")
        if self.dl_btn is not None:
            self.dl_btn.config(state="disabled")
        if self.dl_label is not None:
            self.dl_label.config(text=t("setup.wizard.setupwizard__download_ipa_1"), fg=SUB)
        dest = os.path.join(downloads_dir(), "PhoneCam.ipa")

        def work():
            try:
                download_file(url, dest, "zip")
                self._dl_state = ("ok", dest)
            except Exception as e:
                self._dl_state = ("err", str(e) if isinstance(e, DownloadError) else friendly_net_error(e))

        threading.Thread(target=work, name="ipa-download", daemon=True).start()
        self.top.after(300, self._dl_pump)

    def _dl_pump(self):
        if self.finished:
            return
        st = self._dl_state
        if st and st[0] == "busy":
            self.top.after(300, self._dl_pump)
            return
        try:
            if self.dl_btn is not None:
                self.dl_btn.config(state="normal")
            if st and st[0] == "ok":
                if self.dl_label is not None:
                    self.dl_label.config(text=t("setup.wizard.setupwizard__extra_phonecam_7") % st[1], fg=GOOD)
                _open_folder_select(st[1])
            elif st and self.dl_label is not None:
                self.dl_label.config(text=st[1], fg=BAD)
        except Exception:
            pass

    # -- 状態の自動更新 (2秒ごと) ------------------------------------------
    def _start_pump(self):
        self._next_probe = 0.0
        if self._pump_job is None:
            self._pump_job = self.top.after(200, self._pump)

    def _pump(self):
        self._pump_job = None
        if self.finished or self.page < PAGE_IPHONE:
            return
        try:
            if self._probe_result is not None:
                snap, app = self._probe_result
                self._probe_result = None
                self.snap, self.app_ok = snap, app
                self._next_probe = time.monotonic() + POLL_MS / 1000.0
                if self._apply():
                    return
            if not self._probing and time.monotonic() >= self._next_probe:
                self._probing = True
                want_app = getattr(self, "_step_id", "") == "test"
                linked = bool(self._ctx().get("link"))

                def work():
                    try:
                        snap = iphone.query_usbmux()
                        app = False
                        if want_app and not linked:
                            app = iphone.probe_phonecam(snap)
                        self._probe_result = (snap, app)
                    except Exception:
                        self._probe_result = ({}, False)
                    finally:
                        self._probing = False

                threading.Thread(target=work, name="iphone-probe", daemon=True).start()
        except Exception:
            pass
        self._pump_job = self.top.after(250, self._pump)

    def _render_chip(self):
        if self.chip is None:
            return
        state, text = iphone.step_status(self._step_id, self.snap, self._ctx())
        if state == iphone.DONE:
            self.chip.config(text="✓ " + text, fg=GOOD)
        elif state == iphone.WAIT:
            self.chip.config(text="● " + text, fg=SUB)
        elif state == iphone.MANUAL:
            self.chip.config(text="○ " + text, fg=SUB)
        else:
            self.chip.config(text="! " + text, fg=WARN)
        if self._step_id == "test" and self.next_btn is not None:
            self.next_btn.config(state="normal" if state == iphone.DONE else "disabled")
        return state

    def _apply(self):
        """新しい検出結果を画面へ。自動で次へ進んだら True。"""
        state = self._render_chip()
        if self._first and iphone.should_auto_advance(self._step_id, state, set()):
            self.show(self.page + 1)
            return True
        return False

    # -- 終了 -------------------------------------------------------------
    def _finish(self):
        self._end(self.on_finish)

    def _skip(self):
        self._end(self.on_skip)

    def _skip_iphone(self):
        self._end(self.on_finish if self.mode == "setup" else self.on_skip)

    def _close(self):
        if self.controller.busy:
            return
        if self._guided_active():
            self.controller.guided_abort()
        if self.mode == "guided":
            self._end(self.on_skip)
            return
        if self.page >= PAGE_IPHONE:
            self._skip_iphone()
        else:
            self._skip()

    def _end(self, cb):
        if self.finished:
            return
        self.finished = True
        self.controller.unlisten(self._listener)
        if self._pump_job is not None:
            try:
                self.top.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        try:
            cb()
        except Exception:
            pass

    # -- コントローラーからの通知 ------------------------------------------
    def _on_controller(self, what):
        if self.finished:
            return
        if what == "statuses" or what == "checking":
            if self.page == PAGE_CHECK and self.panel is not None:
                self.panel.render()
                self._sync_mic_option()
                self._update_next_label()
                self._fit_window()
        elif what == "stage" and self.stage_label is not None:
            self.stage_label.config(text=self.controller.stage)
        elif what == "progress" and self.bar is not None:
            self.bar.set(self.controller.progress)
        elif what == "log":
            self._refill_log()
        elif what == "audio" and self.page == PAGE_INSTALL:
            self._show_audio_note()
        elif what == "done" and self.page == PAGE_INSTALL:
            self._show_outcome()
        elif what in ("guided", "guided_done") and self.page == PAGE_INSTALL:
            self._render_guided()
            if what == "guided_done" or not self._guided_active():
                self._show_outcome()
            self._update_install_footer()


def _startfile(path):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass
