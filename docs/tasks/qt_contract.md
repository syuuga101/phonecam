# PhoneCam Qt (PySide6) 移植: ページ担当向けコントラクト

見た目の規則は `ui_contract.md` のまま (Windows 11 設定アプリ風。装飾色なし)。Tk の `ui/` は配布 UI のまま触らない。
Qt 版は `windows/phonecam/qt/` と `windows/phonecam/controller.py`。起動は `python windows/phonecam_qt.py`。

## 担当ファイルとルール
- 自分のページ: `windows/phonecam/qt/pages/<video|quality|effects|audio|recording|status|shortcuts|settings>.py`
  - 公開するのは `build(ctrl) -> QWidget` (通常は `Page`) だけ。`Page` の中に `Section` → `Row` を積む。
  - 使ってよい import: `phonecam.controller` の公開名、`phonecam.qt.widgets`、`phonecam.i18n.t`、
    `phonecam.core.*` の定数 (settings の LENSES/RES など)、`phonecam.ui.labels` (tk 非依存の表示ラベル)、PySide6。
  - 使ってはいけない: `phonecam.ui.app / widgets / theme / icons / tkdeps / pages.*` (Tk 用)、他ページ module、`ctrl` の `_` 付き名。
- 文言は `t("page.section.item")`。キーは `scratchpad/locales/<担当名>.{ja,en}.json` に書く (統合は Opus)。
- 色は直書き禁止。`widgets.secondary_color(palette)` / `error_color` / `ok_color` / `blend` を使う (自前描画の widget は `self.palette()` から)。
- 数値・状態は widget を作ったときに1回読むだけでなく、`ctrl.status_changed` / `ctrl.settings_changed` に connect して更新する。
- 周期処理 (QTimer) は描画用 (プレビュー 15fps / メーター 20fps など) だけ。通信・保存・送信は Controller がやる。
- 見えているときだけ描く: `Page.on_activated()` を上書きするとナビで選ばれたときに呼ばれる。QTimer は `isVisible()` を見て早期 return。

## Controller API (`phonecam/controller.py`, UI/Qt/Tk 非依存)
```python
ctrl = Controller(settings=None, persist=True, demo=False)   # 起動は qt/app.py が作る。ページは受け取るだけ
ctrl.settings   # dict (読み取り可。書き込みは changed() 経由)
ctrl.state      # core.state.SharedState (ctrl.state.snapshot())
ctrl.link / ctrl.audio / ctrl.stream / ctrl.watcher / ctrl.thermal_log   # core のオブジェクトそのまま
ctrl.status     # Status (dict + 属性アクセス)。直近の poll() 結果
```
### 通知 (CallbackList: `.connect(fn)` / `.disconnect(fn)` / `.emit(...)`)。すべて UI スレッドで呼ばれる
| 名前 | 引数 | いつ |
|---|---|---|
| `status_changed` | `(status)` | status の内容が変わったとき (最大 10Hz) |
| `settings_changed` | `(key, value)` | `changed()` が呼ばれたとき (自分の操作でも発火。ウィジェットは「表示と同じなら何もしない」) / iPhone 側操作の取り込み |
| `toast` | `(text, kind)` | 下部に出す短い通知。kind: "info"/"error" |
| `update_changed` | `(banner)` | 更新バナー (`None` か `{"text":..., "actions":[(label, fn)]}`)。まだ中身は未実装 |

### 設定
- `ctrl.get(key, default=None)`  特別キー `"mirror_current"` = 現在のレンズに対応する左右反転 (mirror / mirror_front を自動で選ぶ)。
- `ctrl.changed(key, value, group=None)`  設定を変更。保存は 0.5 秒後、`group` ("video"/"canvas"/"codec"/"screen"/"exposure" など Tk の `_changed` と同じ) を渡すと iPhone へ 50ms 間引いて送る。
- `ctrl.save_now()` / `ctrl.set_theme(mode)` / `ctrl.set_language(lang)`
- 値↔ラベル: `controller.value_to_label(pairs, value)` / `label_to_value(pairs, label)`

### 操作 (Tk の `app._xxx` に相当)
| メソッド | 内容 |
|---|---|
| `toggle_connect() -> str\|None` | 接続/切断。失敗は理由を返し `toast("error")` も出す |
| `start_pause()` | 開始/一時停止 (Tk `_stream_button`) |
| `set_mode("usb"/"wifi")`, `set_ip(ip)`, `set_pair_code(raw) -> 正規化した6桁` | 接続方法・Wi-Fi |
| `set_lens(value, send=True)` | レンズ。mirror も `settings_changed("mirror_current")` で通知 |
| `set_run_mode(v)`, `set_idle_stop(sec)` | iPhone カメラの動かし方 |
| `set_backend(v)`, `set_hw_decode(bool)`, `set_screen(key, value)` | 映像ページ用 (Tk `_on_backend/_hw_changed/_screen_changed`) |
| `toggle_record() -> bool` | 録画開始/停止を依頼 (`status.rec_enabled` / `status.rec_active` でボタン表示) |
| `send_control(dict) -> bool` | iPhone へ任意の control を送る (ズームボタンなど) |
| `apply_audio()` | 音声設定を反映 (mic_enabled / device / exclusive / priority を変えたあと) |
| `meter_db() -> float` | 入力レベル dBFS (ピークホールド付き)。メーターの QTimer から呼ぶ |
| `preview_frame() -> (img, fmt, seq)\|None` | 最新の映像 (numpy HxWx3, fmt "rgb24"/"bgr24")。seq が同じなら描き直し不要 |
| `close()` | 終了処理 (qt/app が呼ぶ) |

### status の項目 (`ctrl.status.<name>`)
`connected, wanted, key("live"/"idle"/"paused"/"off"), state_text, status_text, device("iPhone 13 · USB"), sub, error_text, auth_state,
res_fps, resolution, recv_fps, decode_ms, latency_text, thermal, thermal_text, battery_text, warn_text, phone_muted,
stream_text, camera_text, run_note, watch_text, start_is_pause, start_enabled, idle_enabled, rec_active, rec_enabled,
phone` (= iPhone の status JSON。未接続は `{}`。isoMin/isoMax/zoomMax/focusSupported/torchAvailable/recording/mic など生のキー)。
状態ページが必要とする詳細 (遅延内訳 = `ctrl.link.latency.averages()`、熱履歴 = `ctrl.thermal_log`、音声 = `ctrl.audio.*`) は core のオブジェクトを直接読んでよい。

## qt.widgets
- `Page()` … スクロールする内容列 (最大 760 論理px、左右 24px)。`.add_section(title) -> Section`、`.add(widget)`、`.finish()` (build の最後に必ず)、`.content_width()`、`on_activated()` を上書き可。
- `Section(title)` … `.add_row(icon, title, hint=None) -> Row`、`.add(widget)`、`.add_padded(widget)` (グラフ/メーター等 Row でないものをカードへ)。
- `Row(icon, title, hint)` … `.addControl(widget)` で右側に積む。`.setHint(text)` で補足を出し入れ (空で隠す)、`.setTitle`。
- `Hint(text, wrap=True)` 二次色の小さい説明 / `ErrorLabel(text)` 赤系 (空で隠れる) / `IconLabel(name)` / `button(text, primary=False)` / `Switch`
- 設定に結び付く入力 (表示の同期 + `ctrl.changed` を内蔵):
  - `bound_combo(ctrl, key, [(値, 表示)...] または [値...], group=None, width=None, on_change=None, setter=None)`  (`setter=ctrl.set_run_mode` のように専用メソッドを使える)
  - `bound_switch(ctrl, key, group=None, on_change=None, invert=False)` / `bound_check(ctrl, key, text, group=None)`
  - `bound_slider(ctrl, key, lo, hi, step, fmt=None, group=None, width=180, as_int=False)` → `.slider` / `.value_label`
  - `bound_line(ctrl, key, width=160, placeholder="")`
- 色ヘルパ: `blend, text_color, secondary_color(palette), card_color, border_color, separator_color, error_color, ok_color, is_dark`
- アイコン名 (`qt/icons.py`): link usb wifi video camera lens aspect rotate flip gauge sun droplet focus zoom light sparkles person palette mic speaker volume record folder thermometer battery clock keyboard gear globe download wrench info heart document play pause。足りなければ `_PATHS` に SVG (24 グリッド、stroke のみ) を追記して報告。

## 例
```python
from phonecam.core.settings import RESOLUTIONS
from phonecam.i18n import t
from phonecam.qt.widgets import Page, bound_combo, bound_switch, bound_slider

def build(ctrl):
    page = Page()
    sec = page.add_section(t("video.send.title"))
    sec.add_row("video", t("video.resolution.label")).addControl(
        bound_combo(ctrl, "resolution", list(RESOLUTIONS), group="video"))
    sec.add_row("download", t("video.bitrate.label")).addControl(
        bound_slider(ctrl, "bitrate", 5, 50, 1, lambda v: "%d Mbps" % v, group="bitrate", as_int=True))
    sec.add_row("flip", t("video.mirror.label")).addControl(bound_switch(ctrl, "mirror_current", group=None))
    return page.finish()
```
(`bound_switch(ctrl, "mirror_current")` は `settings_changed("mirror_current")` で同期される。)

## 確認
- py_compile だけ必須 (ここでは PySide6 を動かせない)。見た目は CI の `qt-screenshots` ジョブ (artifact "qt-screens": 全ページ x ライト/ダーク x 1.0/2.0 倍 + スクロール) を見て直す。
- ローカル (PySide6 のある Windows): `python windows/phonecam_qt.py` / `python windows/qt_smoke.py --out qt-screens`。
- demo コントローラ: `Controller.demo_connected()` (iPhone 13 / USB / 1080p60 / 31ms / 正常 / 82%)。
