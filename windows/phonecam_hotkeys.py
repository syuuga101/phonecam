"""PhoneCam: グローバルショートカットキー (OBS やゲームを操作中でも効く)。

Windows の RegisterHotKey を、専用スレッドのメッセージループ (GetMessageW) で受ける。
キーが押されると動作名をキューに入れるだけで、実際の処理は呼び出し側 (Tk スレッド) が
キューを定期的に取り出して行う。Windows 以外では何もしない。

キーの保存形式は "ctrl+alt+c" のような小文字の文字列 (空文字は「なし」)。
"""
import queue
import sys
import threading

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP = 0x8000
WM_USER_APPLY = WM_APP + 1

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# 動作名 → (表示名, 既定のキー)
ACTIONS = [
    ("camera", "カメラ切り替え（広角→超広角→インカメ）", "ctrl+alt+c"),
    ("mic", "マイクのミュート切り替え", "ctrl+alt+m"),
    ("blur", "背景ぼかし オン/オフ", "ctrl+alt+b"),
    ("pause", "カメラの開始/一時停止", "ctrl+alt+p"),
    ("rec", "録画の開始/停止", "ctrl+alt+r"),
    ("zoom_in", "ズームを上げる (+0.25)", "ctrl+alt+up"),
    ("zoom_out", "ズームを下げる (-0.25)", "ctrl+alt+down"),
]
ACTION_NAMES = [a for a, _, _ in ACTIONS]
ACTION_LABELS = {a: l for a, l, _ in ACTIONS}
DEFAULT_HOTKEYS = {a: k for a, _, k in ACTIONS}

MOD_ORDER = ("ctrl", "alt", "shift", "win")
MOD_FLAGS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# キー名 → 仮想キーコード
KEY_VK = {}
for _i in range(26):
    KEY_VK[chr(ord("a") + _i)] = 0x41 + _i
for _i in range(10):
    KEY_VK[str(_i)] = 0x30 + _i
for _i in range(1, 13):
    KEY_VK["f%d" % _i] = 0x70 + _i - 1
KEY_VK.update({"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
               "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24, "space": 0x20})
VK_KEY = {v: k for k, v in KEY_VK.items()}
KEY_DISPLAY = {"up": "↑", "down": "↓", "left": "←", "right": "→", "pageup": "PageUp",
               "pagedown": "PageDown", "home": "Home", "end": "End", "space": "Space"}

# Tk の keysym → キー名 (文字・数字・F キーはそのまま小文字で引ける)
TK_KEYSYM = {"Up": "up", "Down": "down", "Left": "left", "Right": "right",
             "Prior": "pageup", "Next": "pagedown", "Home": "home", "End": "end", "space": "space"}

# Tk (Windows) のキーイベントの state ビット
TK_SHIFT = 0x0001
TK_CONTROL = 0x0004
TK_ALT = 0x20000


def is_windows():
    return sys.platform == "win32"


def parse_combo(text):
    """"ctrl+alt+c" → (修飾キーのフラグ, 仮想キーコード, 正規化した文字列)。不正・空は None。
    修飾キーが1つも無いものは不正 (単独のキーを乗っ取らないため)。"""
    if not isinstance(text, str) or not text.strip():
        return None
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    mods = []
    key = None
    for p in parts:
        if p in MOD_FLAGS:
            if p not in mods:
                mods.append(p)
        elif p in KEY_VK and key is None:
            key = p
        else:
            return None
    if key is None or not mods:
        return None
    mods.sort(key=MOD_ORDER.index)
    flags = 0
    for m in mods:
        flags |= MOD_FLAGS[m]
    return flags, KEY_VK[key], "+".join(mods + [key])


def normalize_combo(text):
    """正規化した文字列を返す。不正なら ""。"""
    r = parse_combo(text)
    return r[2] if r else ""


def combo_text(text):
    """表示用: "ctrl+alt+up" → "Ctrl + Alt + ↑"。空は「なし」。"""
    r = parse_combo(text)
    if not r:
        return "なし"
    parts = r[2].split("+")
    out = [MOD_DISPLAY[p] for p in parts[:-1]]
    k = parts[-1]
    out.append(KEY_DISPLAY.get(k, k.upper()))
    return " + ".join(out)


def sanitize_hotkeys(value):
    """設定から読んだショートカットを検証する。足りない動作は既定値。不正値は既定値。
    同じキーが複数の動作にあるときは、先のものだけ残す。"""
    src = value if isinstance(value, dict) else {}
    out = {}
    used = set()
    for action in ACTION_NAMES:
        if action in src:
            raw = src[action]
            combo = "" if raw in ("", None) else normalize_combo(raw)
            if raw not in ("", None) and not combo:
                combo = DEFAULT_HOTKEYS[action]
        else:
            combo = DEFAULT_HOTKEYS[action]
        if combo and combo in used:
            combo = ""
        if combo:
            used.add(combo)
        out[action] = combo
    return out


def combo_from_tk_event(state, keysym, keycode=None, win_down=False):
    """Tk のキーイベントからキー文字列を作る。修飾キー単独・対応外のキー・修飾キー無しは None。
    keycode は Windows の Tk では仮想キーコードそのもの (Shift+数字で keysym が記号になるときに使う)。"""
    name = TK_KEYSYM.get(keysym)
    if name is None and isinstance(keysym, str):
        low = keysym.lower()
        if low in KEY_VK:
            name = low
    if name is None and isinstance(keycode, int):
        name = VK_KEY.get(keycode)
    if name is None:
        return None
    mods = []
    if state & TK_CONTROL:
        mods.append("ctrl")
    if state & TK_ALT:
        mods.append("alt")
    if state & TK_SHIFT:
        mods.append("shift")
    if win_down:
        mods.append("win")
    if not mods:
        return None
    return "+".join(mods + [name])


def win_key_down():
    """Windows キーが押されているか (Tk のイベントには出ないので GetAsyncKeyState で見る)。"""
    if not is_windows():
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        return bool((u.GetAsyncKeyState(0x5B) & 0x8000) or (u.GetAsyncKeyState(0x5C) & 0x8000))
    except Exception:
        return False


class HotkeyManager:
    """RegisterHotKey を専用スレッドで持つ。apply() で登録し直し、poll() で押された動作を受け取る。

    user32 を渡すとテストで差し替えられる (ctypes.windll.user32 と同じ関数を持つもの)。
    """

    def __init__(self, user32=None, enabled=None):
        self._user32 = user32
        self._enabled = is_windows() if enabled is None else enabled
        self.events = queue.Queue()      # 押された動作名
        self._cmd = queue.Queue()        # (bindings, 結果を返す queue)
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._registered = {}            # id -> action (スレッド内だけで使う)
        self.results = {}                # action -> True(登録できた) / False(他のアプリが使用中) / None(なし)

    @property
    def available(self):
        return self._enabled

    def _u32(self):
        if self._user32 is None:
            import ctypes
            self._user32 = ctypes.windll.user32
        return self._user32

    # -- 公開 ------------------------------------------------------------
    def start(self):
        if not self._enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def apply(self, bindings, timeout=2.0):
        """bindings: {動作名: "ctrl+alt+c" or ""}。登録し直して結果 {動作名: True/False/None} を返す。
        空の bindings で全部解除 (マスタースイッチをオフにしたとき)。"""
        if not self._enabled:
            self.results = {a: None for a in bindings}
            return dict(self.results)
        self.start()
        if self._thread is None or self._tid is None:
            self.results = {a: None for a in bindings}
            return dict(self.results)
        box = queue.Queue()
        self._cmd.put((dict(bindings), box))
        try:
            self._u32().PostThreadMessageW(self._tid, WM_USER_APPLY, 0, 0)
            res = box.get(timeout=timeout)
        except Exception:
            res = {a: None for a in bindings}
        self.results = res
        return dict(res)

    def poll(self):
        """押された動作名を取り出す (無ければ None)。Tk スレッドから呼ぶ。"""
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        """全部解除してスレッドを止める。"""
        t = self._thread
        if t is None:
            return
        try:
            self._u32().PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        t.join(1.0)
        self._thread = None
        self._tid = None

    # -- スレッド ----------------------------------------------------------
    def _run(self):
        try:
            import ctypes
            from ctypes import wintypes
            u = self._u32()
            msg = wintypes.MSG()
            # メッセージキューを先に作る (PostThreadMessage が届くように)
            u.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, 0)
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        except Exception:
            self._enabled = False
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                r = u.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:  # 0 = WM_QUIT / -1 = エラー
                    break
                if msg.message == WM_HOTKEY:
                    action = self._registered.get(int(msg.wParam))
                    if action:
                        self.events.put(action)
                elif msg.message == WM_USER_APPLY:
                    self._drain_commands(u)
        except Exception:
            pass
        finally:
            self._unregister_all(u)

    def _unregister_all(self, u):
        for hid in list(self._registered):
            try:
                u.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._registered.clear()

    def _drain_commands(self, u):
        while True:
            try:
                bindings, box = self._cmd.get_nowait()
            except queue.Empty:
                return
            box.put(self._apply_in_thread(u, bindings))

    def _apply_in_thread(self, u, bindings):
        self._unregister_all(u)
        res = {}
        next_id = 1
        for action, text in bindings.items():
            parsed = parse_combo(text)
            if not parsed:
                res[action] = None
                continue
            flags, vk, _ = parsed
            ok = bool(u.RegisterHotKey(None, next_id, flags | MOD_NOREPEAT, vk))
            if ok:
                self._registered[next_id] = action
                next_id += 1
            res[action] = ok
        return res
