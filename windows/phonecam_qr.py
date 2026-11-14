# -*- coding: utf-8 -*-
"""依存なしの小さな QR コード生成 (バイトモード・バージョン1〜10・誤り訂正 L/M)。

画像ライブラリ (PIL など) は使わず、モジュール (黒/白のマス) の2次元配列を返します。
画面への描画は phonecam/qt/setup_common.py の QrView が行います。
"""

# バージョンごとの (誤り訂正コード語数/ブロック, [(ブロック数, データ語数/ブロック), ...])
_EC = {
    "L": {1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]), 5: (26, [(1, 108)]),
          6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]), 9: (30, [(2, 116)]),
          10: (18, [(2, 68), (2, 69)])},
    "M": {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
          6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])},
}
_FORMAT_BITS = {"L": 1, "M": 0}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
MAX_VERSION = 10

# --- GF(256) (原始多項式 0x11D) ---------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def rs_generator(n):
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        poly = nxt
    return poly  # 最高次の係数が先頭


def rs_encode(data, n):
    """data (整数のリスト) に対する n 個の誤り訂正コード語を返す。"""
    gen = rs_generator(n)
    rem = [0] * n
    for b in data:
        factor = b ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(n):
            rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


def _capacity(version, level):
    ec, groups = _EC[level][version]
    return sum(n * d for n, d in groups)


def choose_version(nbytes, level):
    for v in range(1, MAX_VERSION + 1):
        head = 4 + (8 if v <= 9 else 16)
        if head + 8 * nbytes <= 8 * _capacity(v, level):
            return v
    return None


def _data_codewords(data, version, level):
    cap = _capacity(version, level)
    bits = [0, 1, 0, 0]
    cc = 8 if version <= 9 else 16
    bits += [(len(data) >> (cc - 1 - i)) & 1 for i in range(cc)]
    for byte in data:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    bits += [0] * min(4, 8 * cap - len(bits))
    while len(bits) % 8:
        bits.append(0)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(words) < cap:
        words.append(pad[i % 2])
        i += 1
    return words


def _interleave(words, version, level):
    ec_n, groups = _EC[level][version]
    blocks = []
    pos = 0
    for count, dlen in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + dlen])
            pos += dlen
    ecs = [rs_encode(b, ec_n) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_n):
        for e in ecs:
            out.append(e[i])
    return out


def _bch_format(level, mask):
    data = (_FORMAT_BITS[level] << 3) | mask
    rem = data << 10
    while rem.bit_length() >= 11:
        rem ^= 0x537 << (rem.bit_length() - 11)
    return ((data << 10) | rem) ^ 0x5412


def _bch_version(version):
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


class _Grid:
    def __init__(self, version):
        self.v = version
        self.size = 17 + 4 * version
        n = self.size
        self.m = [[False] * n for _ in range(n)]
        self.f = [[False] * n for _ in range(n)]  # 機能パターン (データを置かない場所)

    def put(self, x, y, dark, func=True):
        self.m[y][x] = bool(dark)
        if func:
            self.f[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):  # タイミング
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):  # 位置検出
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self.put(x, y, d not in (2, 4))
        pos = _ALIGN[self.v]
        for i, cy in enumerate(pos):  # 位置合わせ
            for j, cx in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format("L", 0)  # 場所の予約 (あとで上書き)
        if self.v >= 7:
            bits = _bch_version(self.v)
            for i in range(18):
                b = (bits >> i) & 1
                a = n - 11 + i % 3
                c = i // 3
                self.put(a, c, b)
                self.put(c, a, b)

    def draw_format(self, level, mask):
        bits = _bch_format(level, mask)
        n = self.size
        g = lambda i: (bits >> i) & 1
        for i in range(6):
            self.put(8, i, g(i))
        self.put(8, 7, g(6))
        self.put(8, 8, g(7))
        self.put(7, 8, g(8))
        for i in range(9, 15):
            self.put(14 - i, 8, g(i))
        for i in range(8):
            self.put(n - 1 - i, 8, g(i))
        for i in range(8, 15):
            self.put(8, n - 15 + i, g(i))
        self.put(8, n - 8, True)  # 常に黒のモジュール

    def place_data(self, codewords):
        bits = []
        for w in codewords:
            bits += [(w >> (7 - i)) & 1 for i in range(8)]
        n = self.size
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.f[y][x] and i < len(bits):
                        self.m[y][x] = bool(bits[i])
                        i += 1
            right -= 2


def _penalty(m):
    n = len(m)
    score = 0
    lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    for line in lines:  # N1 連続 / N3 パターン
        run = 1
        for i in range(1, n):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
        s = "".join("1" if c else "0" for c in line)
        for pat in ("10111010000", "00001011101"):
            start = 0
            while True:
                k = s.find(pat, start)
                if k < 0:
                    break
                score += 40
                start = k + 1
    for y in range(n - 1):  # N2 2x2
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(1 for row in m for c in row if c)
    pct = dark * 100.0 / (n * n)
    score += int(abs(pct - 50) / 5) * 10  # N4
    return score


def make_qr(text, level="M", mask=None):
    """text を QR にして modules (行のリスト。True=黒) を返す。入らなければ ValueError。"""
    data = text.encode("utf-8")
    for lv in ([level] + [x for x in ("M", "L") if x != level]):
        version = choose_version(len(data), lv)
        if version is not None:
            level = lv
            break
    else:
        raise ValueError("QRコードにするには文字列が長すぎます")
    words = _interleave(_data_codewords(data, version, level), version, level)
    base = _Grid(version)
    base.draw_function_patterns()
    base.place_data(words)
    best = None
    for k in ([mask] if mask is not None else range(8)):
        grid = [row[:] for row in base.m]
        for y in range(base.size):
            for x in range(base.size):
                if not base.f[y][x] and _MASKS[k](y, x):
                    grid[y][x] = not grid[y][x]
        g = _Grid(version)
        g.m = grid
        g.f = [row[:] for row in base.f]
        g.draw_format(level, k)
        sc = _penalty(g.m)
        if best is None or sc < best[0]:
            best = (sc, k, g.m)
    return best[2]
