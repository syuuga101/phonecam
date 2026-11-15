# 更新の署名（update-manifest.json）の鍵と手順

Windows 版の自動更新は、リリースに付く 2 つのファイルを使って、ダウンロードした exe が正規のものか確かめます。

| ファイル | 中身 |
| --- | --- |
| `update-manifest.json` | `{"asset", "version", "sha256", "size"}`（exe の SHA-256 とサイズ） |
| `update-manifest.json.sig` | 上のファイルの Ed25519 署名（Base64） |

`release.yml` が、リリースのたびに exe からマニフェストを作り、Secret `UPDATE_SIGNING_KEY`（秘密鍵）があれば署名を付けます。アプリは、埋め込んだ公開鍵（`windows/phonecam_update.py` の `UPDATE_PUBLIC_KEY`）で署名を確かめます。

## いまの状態（移行中）

公開鍵がまだ空なので、アプリは署名を使わず、これまでどおり「GitHub が返す SHA-256 と一致すること」だけで更新します。ただし、ハッシュが無いときは更新を中止します（以前はサイズと `MZ` だけで通っていました）。

鍵を作って公開鍵をアプリに入れると、次のリリースから署名が必須になります。

## 鍵を作る（1 回だけ）

リポジトリのルートで実行します。Python 3.8 以上だけで動き、追加のライブラリは要りません。

```
python -c "import os, base64, sys; sys.path.insert(0, 'windows'); import phonecam_update as u; s = os.urandom(32); print('UPDATE_SIGNING_KEY =', base64.b64encode(s).decode()); print('UPDATE_PUBLIC_KEY =', base64.b64encode(u.ed25519_public_key(s)).decode())"
```

2 行が出ます。

- `UPDATE_SIGNING_KEY`（秘密鍵）: 誰にも見せない。リポジトリにコミットしない。画面や履歴に残ったら、作り直す。
- `UPDATE_PUBLIC_KEY`（公開鍵）: アプリに埋め込む。公開してよい。

## 登録する

1. GitHub のリポジトリで Settings → Secrets and variables → Actions → New repository secret を開く。
2. 名前を `UPDATE_SIGNING_KEY`、値を秘密鍵（Base64 の 1 行）にして保存する。
3. `windows/phonecam_update.py` の `UPDATE_PUBLIC_KEY = ""` に、公開鍵を貼る。
4. PR を作ってマージし、`VERSION` を上げてリリースする。リリースに `update-manifest.json` と `update-manifest.json.sig` が付いていれば成功。

公開鍵を入れた版をリリースする前に、必ず Secret を登録してください。公開鍵が入っているのに Secret が無い、または公開鍵と対になっていないときは、`release.yml` の「Create update manifest」の手順が失敗してリリースが止まります（署名の無いリリースが公開されて、利用者の自動更新が止まるのを防ぐため）。

## 公開鍵を入れたあとの動き

- 署名つきのマニフェストが無い、署名が合わない、マニフェストの版や exe の SHA-256 が違う、GitHub が返すハッシュと食い違う場合は、すべて更新を中止します（旧方式へは戻りません）。
- 更新を中止しても、アプリは今のまま動きます。リリースページから手動で更新できます。
- すでに配った古い版（公開鍵なし）は、これまでどおりハッシュだけで更新します。署名が効くのは、公開鍵が入った版からです。

## 鍵を作り直す（漏れた・紛失したとき）

アプリは埋め込んだ公開鍵でしか署名を確かめないので、鍵を替えるには、新しい公開鍵を入れた版を利用者に届ける必要があります。

1. 新しい鍵を作る（上の手順）。まだ Secret は替えない（旧鍵のまま）。
2. 新しい公開鍵を `UPDATE_PUBLIC_KEY` に入れた版を、**旧鍵で署名して**リリースする。古い版のアプリは旧い公開鍵でしか確かめられないためです。`release.yml` は、署名の鍵と埋め込んだ公開鍵が対でないと失敗するので、この 1 回だけ、リポジトリの Variables（Settings → Secrets and variables → Actions → Variables）に `UPDATE_KEY_ROTATION` = `1` を作ってからリリースし、終わったら消す。
3. 利用者が新しい公開鍵の入った版に更新したあと、Secret を新しい秘密鍵に替える。

秘密鍵が漏れたときは、その鍵で署名された更新を信用できません。リリースページで案内して、新しい版を手動で入れてもらう方が安全です。

## 仕組みと選んだ理由

- 署名の方式は Ed25519（RFC 8032）。検証と署名は `phonecam_update.py` の純 Python の実装で行います。`cryptography` は依存に入っておらず、入れると exe が数 MB 増えます。PyNaCl も同様に別のネイティブ部品が増えます。更新のときに 1 回検証するだけなので、標準ライブラリだけの実装（exe の増加はほぼ 0 バイト、検証は数ミリ秒）にしました。RFC 8032 のテストベクトルと、`cryptography` の結果との一致を `windows/tests/test_hardening.py` で確かめています。
- 署名の対象は、マニフェストのバイト列そのものです。署名の確認と JSON の解釈を同じバイト列で行います。
- マニフェストには版が入っていて、アプリは「更新しようとしているリリースの版」と一致することを確かめます。古い版の署名を新しい版の更新に使い回す攻撃を防ぎます。
- この仕組みは、exe に Authenticode のコード署名を付けるものではありません。Windows の SmartScreen の警告は変わりません。
