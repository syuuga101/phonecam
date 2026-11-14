#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SideStore / AltStore 共通のソース JSON を作る。

SideStore は AltStore の派生で、同じ形式のソースを読む。そのため同じ内容のファイルを
altstore-source.json (従来の URL。AltStore で入れた人のため残す) と
sidestore-source.json (SideStore 向けの名前) の2つで出す。違うのは sourceURL だけ。

使い方:
  make_altstore_source.py --repo OWNER/NAME --version 1.3.0 --build 42 --ipa out/PhoneCam.ipa \
      --out out/altstore-source.json --also-out out/sidestore-source.json

downloadURL は、そのタグの Release に添付した PhoneCam.ipa の URL にする。
ソース自体は releases/latest/download/altstore-source.json から配る (常に最新のリリースを指す)。
"""

import argparse
import datetime
import json
import os
import sys

BUNDLE_ID = "com.example.phonecam"  # ios/project.yml の PRODUCT_BUNDLE_IDENTIFIER と同じ
SOURCE_ID = "io.github.syuuga101.phonecam"
MIN_OS = "17.0"
TINT = "#000000"
DESCRIPTION = ("iPhoneを、WindowsのOBSで使える低遅延のワイヤレス／USBカメラにするアプリです。"
               "カメラの映像をH.264/HEVCでPCへ送ります。PCの受信アプリ（PhoneCamReceiver）と一緒に使います。")
# Info.plist の利用目的 (ios/project.yml と同じ文言)。AltStore はソースに権限の一覧を求める
PRIVACY = {
    "NSCameraUsageDescription": "配信用カメラとして使用します",
    "NSLocalNetworkUsageDescription": "Wi-Fi経由でPCと接続するために使用します",
    "NSMicrophoneUsageDescription": "iPhoneのマイク音声をPCへ送るため、録画に音声を入れるために使用します",
    "NSPhotoLibraryAddUsageDescription": "録画した動画を写真アプリに保存するために使用します",
}


def source_url(repo, filename):
    return "https://github.com/%s/releases/latest/download/%s" % (repo, filename)


def build_source(repo, version, build, ipa_size, date=None, tag=None, ipa_name="PhoneCam.ipa",
                 filename="altstore-source.json"):
    owner = repo.split("/")[0]
    tag = tag or "v%s" % version
    date = date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    icon = "https://raw.githubusercontent.com/%s/main/docs/icon.png" % repo
    ipa_url = "https://github.com/%s/releases/download/%s/%s" % (repo, tag, ipa_name)
    notes = "PhoneCam v%s" % version
    app = {
        "name": "PhoneCam",
        "bundleIdentifier": BUNDLE_ID,
        "developerName": owner,
        "subtitle": "iPhoneをOBSのカメラにする",
        "localizedDescription": DESCRIPTION,
        "iconURL": icon,
        "tintColor": TINT,
        "category": "photo-video",
        "screenshots": [],
        "appPermissions": {"entitlements": ["get-task-allow"], "privacy": dict(PRIVACY)},
        "versions": [{
            "version": version,
            "buildVersion": str(build),
            "marketingVersion": version,
            "date": date,
            "localizedDescription": notes,
            "downloadURL": ipa_url,
            "size": int(ipa_size),
            "minOSVersion": MIN_OS,
        }],
        # 古い形式 (AltStore 1.x 系) のフィールド。新しい AltStore は versions を使い、これは無視する
        "version": version,
        "versionDate": date,
        "versionDescription": notes,
        "downloadURL": ipa_url,
        "size": int(ipa_size),
    }
    return {
        "name": "PhoneCam",
        "identifier": SOURCE_ID,
        "subtitle": "iPhoneをOBSのカメラにする",
        "description": DESCRIPTION,
        "iconURL": icon,
        "website": "https://github.com/%s" % repo,
        "sourceURL": source_url(repo, filename),
        "tintColor": TINT,
        "nsfw": False,
        "apps": [app],
        "featuredApps": [BUNDLE_ID],
        "news": [{
            "identifier": "release-%s" % version,
            "title": "PhoneCam v%s" % version,
            "caption": "新しいバージョンを公開しました",
            "date": date,
            "tintColor": TINT,
            "notify": False,
            "url": "https://github.com/%s/releases/tag/%s" % (repo, tag),
            "appID": BUNDLE_ID,
        }],
        "userInfo": {},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="OWNER/NAME")
    ap.add_argument("--version", required=True)
    ap.add_argument("--build", required=True, help="IPA の CFBundleVersion (CURRENT_PROJECT_VERSION)")
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--also-out", default=None, help="同じ内容をもう1つ書く (sidestore-source.json)")
    ap.add_argument("--date", default=None)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args(argv)
    if "/" not in a.repo:
        ap.error("--repo は OWNER/NAME の形で指定してください")
    size = os.path.getsize(a.ipa)
    if size <= 0:
        ap.error("IPA が空です")
    date = a.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for path in [p for p in (a.out, a.also_out) if p]:
        data = build_source(a.repo, a.version.strip(), a.build, size, date, a.tag,
                            os.path.basename(a.ipa), os.path.basename(path))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("wrote %s (%s v%s, %d bytes)" % (path, a.repo, a.version, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
