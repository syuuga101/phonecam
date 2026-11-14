#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_altstore_source.py が作った JSON を、SideStore / AltStore が読める形か確かめる。使い方: check_source.py FILE [FILE ...]"""
import json
import re
import sys


def check(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for k in ("name", "identifier", "sourceURL", "apps", "news", "nsfw"):
        assert k in d, "%s: トップに %s がありません" % (path, k)
    assert d["sourceURL"].endswith("/" + path.replace("\\", "/").split("/")[-1]), "%s: sourceURL がファイル名と合いません" % path
    assert isinstance(d["apps"], list) and len(d["apps"]) == 1
    app = d["apps"][0]
    for k in ("name", "bundleIdentifier", "developerName", "localizedDescription", "iconURL", "versions", "appPermissions"):
        assert app.get(k) not in (None, "", []), "%s: app.%s がありません" % (path, k)
    assert set(app["appPermissions"]) >= {"entitlements", "privacy"}
    assert d["featuredApps"] == [app["bundleIdentifier"]]
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", d["tintColor"]) and re.fullmatch(r"#[0-9A-Fa-f]{6}", app["tintColor"])
    v = app["versions"][0]
    for k in ("version", "buildVersion", "date", "downloadURL", "size", "minOSVersion"):
        assert v.get(k) not in (None, ""), "%s: versions[0].%s がありません" % (path, k)
    assert isinstance(v["size"], int) and v["size"] > 0
    assert v["downloadURL"].startswith("https://") and v["downloadURL"].endswith(".ipa")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", v["date"])
    # AltStore Classic 1.x 向けの古い項目も残っていること
    for k in ("version", "versionDate", "downloadURL", "size"):
        assert app[k] == v.get({"versionDate": "date"}.get(k, k)), "%s: 古い項目 %s が versions と食い違います" % (path, k)
    for n in d["news"]:
        for k in ("identifier", "title", "caption", "date"):
            assert n.get(k), "%s: news.%s がありません" % (path, k)
    return d


def main(argv):
    docs = [check(p) for p in argv]
    # 2つのファイルは sourceURL 以外が同じ内容であること
    if len(docs) > 1:
        strip = lambda d: {k: v for k, v in d.items() if k != "sourceURL"}
        assert all(strip(x) == strip(docs[0]) for x in docs), "ファイル同士の内容が違います"
    print("OK: %s" % ", ".join(argv))


if __name__ == "__main__":
    main(sys.argv[1:])
