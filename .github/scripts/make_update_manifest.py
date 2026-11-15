#!/usr/bin/env python3
"""リリースの更新マニフェスト (update-manifest.json) と、その署名 (update-manifest.json.sig) を作る。

マニフェスト: {"asset", "version", "sha256", "size"} (exe の SHA-256)。
環境変数 UPDATE_SIGNING_KEY (Ed25519 の秘密鍵 = 32 バイトの種を Base64 にしたもの) があれば署名を付ける。
無いときはマニフェストだけを作り、署名は付けない (アプリの公開鍵が空の間はそれで足りる)。
鍵の作り方・登録の手順は docs/release-signing.md。
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "windows"))
import phonecam_update as update  # noqa: E402  (標準ライブラリだけで動く。署名と検証は同じ実装を使う)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print("::error::version は x.y.z の形式にしてください", file=sys.stderr)
        return 1
    with open(args.exe, "rb") as f:
        data = f.read()
    manifest = {"asset": update.ASSET_NAME, "version": args.version,
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    body = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, update.MANIFEST_NAME)
    with open(manifest_path, "wb") as f:
        f.write(body)
    print("wrote %s (sha256 %s)" % (manifest_path, manifest["sha256"]))
    key = (os.environ.get("UPDATE_SIGNING_KEY") or "").strip()
    sig_path = os.path.join(args.out, update.MANIFEST_SIG_NAME)
    if not key:
        if update.UPDATE_PUBLIC_KEY.strip():
            # 公開鍵が入った版は、署名の無い更新を受け付けない。署名なしで公開すると、利用者の自動更新が止まるので、ここで止める
            print("::error::アプリに公開鍵が入っているのに UPDATE_SIGNING_KEY がありません (docs/release-signing.md)", file=sys.stderr)
            return 1
        print("::notice::UPDATE_SIGNING_KEY が未設定なので、署名は付けません (アプリに公開鍵が入るまでは、これで足ります)")
        if os.path.exists(sig_path):
            os.remove(sig_path)
        return 0
    try:
        seed = base64.b64decode(key, validate=True)
        if len(seed) != 32:
            raise ValueError("length")
    except Exception:
        print("::error::UPDATE_SIGNING_KEY は 32 バイトの種を Base64 にした値にしてください", file=sys.stderr)
        return 1
    sig = update.ed25519_sign(seed, body)
    # 公開鍵がアプリに埋め込まれているときは、署名がその鍵で通ることをリリース前に確かめる (鍵の取り違いで更新が止まるのを防ぐ)
    embedded = update.decode_public_key(update.UPDATE_PUBLIC_KEY)
    rotating = os.environ.get("UPDATE_KEY_ROTATION") == "1"   # 公開鍵を入れ替える版だけ、旧鍵で署名するために対の確認を省く
    if embedded is not None and not rotating and not update.ed25519_verify(embedded, body, sig):
        print("::error::UPDATE_SIGNING_KEY が、アプリに埋め込んだ公開鍵 (UPDATE_PUBLIC_KEY) と対になっていません", file=sys.stderr)
        return 1
    if not update.ed25519_verify(update.ed25519_public_key(seed), body, sig):
        print("::error::署名の自己検証に失敗しました", file=sys.stderr)
        return 1
    with open(sig_path, "w", encoding="ascii") as f:
        f.write(base64.b64encode(sig).decode("ascii") + "\n")
    print("wrote %s" % sig_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
