"""LAN pairing and discovery. Bonjour only lists candidates; the peer is proven by the v2 handshake (core/secure.py)."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import re
import secrets
import socket
import subprocess
from threading import Event, Lock, Thread
import time
from urllib.parse import urlencode
from uuid import UUID

from phonecam.core.protocol import MSG_CONTROL, MSG_SECURE, PhoneCamError, encode_msg, read_msg
from phonecam.core.secure import HS_SECONDS, QrResponder, fingerprint, hs_parse, is_hex, parse_peer, read_hs

VERSION = 2
SERVICE = "_phonecam._tcp.local."
PAIR_SECONDS = 180
TETHER = "172.20.10.1"
HOTSPOT = ipaddress.IPv4Network("192.168.137.0/24")


def ipv4(values):
    if not isinstance(values, (list, tuple)):
        return ()
    addresses = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            ip = ipaddress.IPv4Address(value)
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_global):
            addresses.append(str(ip))
    return tuple(dict.fromkeys(addresses))


def device_id(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return ""


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    addresses: tuple
    code: str = ""     # v1 の登録の古い PIN。v2 では送らない。v2 でペアリングした時点で消す
    model: str = ""    # 機種名 (iPhone 側が送る。古いアプリでは空)
    alias: str = ""    # PC 側で付けた名前 (登録済み端末のみ。iPhone から届いた名前より優先)
    key: str = ""      # ペアリングで保存した iPhone の長期公開鍵 (X25519。16 進 64 文字)
    repair: bool = False   # v1 で登録した端末 (鍵が無い): 再ペアリングが必要

    @property
    def title(self):
        return self.alias or self.name

    @property
    def paired(self):
        return bool(self.key)

    @property
    def needs_repair(self):
        return self.repair and not self.key

    @property
    def registered(self):
        """登録済み (ペアリング済み、または v1 の登録で再ペアリングが必要)。見つかっただけの端末は False。"""
        return bool(self.key) or self.repair

    @property
    def fp(self):
        return fingerprint(bytes.fromhex(self.key)) if self.key else ""

    def stored(self):
        data = {"id": self.id, "name": self.name, "addresses": list(self.addresses)}
        if self.key:
            data["key"] = self.key
            data["fp"] = self.fp
        elif self.repair:
            data["repair"] = True      # v1 の登録: 再ペアリングが必要
            if self.code:
                data["code"] = self.code   # v2 でペアリングするまで残す (古い版へ戻しても使えるように)。送信には使わない
        if self.model:
            data["model"] = self.model
        if self.alias:
            data["alias"] = self.alias
        return data


def _text(value, limit=128):
    return value.strip()[:limit] if isinstance(value, str) else ""


def read_device(data):
    if not isinstance(data, dict):
        return None
    ident = device_id(data.get("id"))
    addresses = ipv4(data.get("addresses"))
    name, code, key = data.get("name"), data.get("code"), data.get("key")
    if not (ident and isinstance(name, str)):
        return None
    if isinstance(key, str) and is_hex(key, 32):
        key, code = key.lower(), ""
    elif key in (None, ""):
        key = ""
        code = code if isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) else ""
        if not code and data.get("repair") is not True:
            return None    # 鍵も、v1 の登録の印も無いものは使えない
    else:
        return None
    if not key and not addresses:
        return None    # v1 の登録は、つなぐ先 (IP) が無いと再ペアリングもできない
    return Device(ident, name.strip()[:128], addresses[:32], code, _text(data.get("model")), _text(data.get("alias")), key,
                  repair=not key)


class Pairing:
    """QR 方式の一時リスナー (QR ダイアログが持つ)。一回限り・短時間。
    QR に載せたアドレスだけで待ち受け (0.0.0.0 では待たない)、送信元ごとに 1 本までしか受けない。
    iPhone は PC の長期公開鍵のフィンガープリントを QR で知っており、それと合う相手にだけ自分の鍵とトークンを
    暗号化した経路で渡す。PIN は送らない (docs/PROTOCOL.md)。"""

    MAX_ACTIVE = 8

    def __init__(self, addresses, on_pair, identity, seconds=PAIR_SECONDS, pc_name=None):
        if not addresses:
            raise ValueError("No IPv4 addresses")
        self.identity = identity
        self.pc_name = pc_name or socket.gethostname()[:128]
        self._stop = Event()
        self._lock = Lock()
        self._used = False
        self._active = 0
        self._sources = set()
        self._reply = b""
        self._expires = time.monotonic() + seconds
        self._token = secrets.token_urlsafe(24)
        self._listeners = []
        try:
            port = 0
            for address in addresses:
                listener = socket.socket()
                try:
                    listener.bind((address, port))
                except OSError:
                    listener.close()
                    continue
                listener.listen(4)
                listener.settimeout(0.25)
                port = port or listener.getsockname()[1]
                self._listeners.append((listener, address))
            if not self._listeners:
                raise OSError("No address could be bound")
            self.bound = tuple(address for _listener, address in self._listeners)
            self.port = port
            self.url = "phonecam://pair?" + urlencode({
                "ips": ",".join(self.bound), "port": port, "token": self._token,
                "name": self.pc_name, "v": VERSION, "fp": identity.fp,
            })
            for listener, _address in self._listeners:
                Thread(target=self._serve, args=(listener, on_pair), daemon=True).start()
        except BaseException:
            self.close()
            raise

    def close(self):
        self._stop.set()
        for listener, _address in self._listeners:
            try:
                listener.close()
            except OSError:
                pass

    def _serve(self, listener, on_pair):
        try:
            while not self._stop.is_set() and time.monotonic() < self._expires:
                try:
                    conn, peer = listener.accept()
                except socket.timeout:
                    continue
                with self._lock:
                    busy = self._active >= self.MAX_ACTIVE or peer[0] in self._sources
                    if not busy:
                        self._active += 1
                        self._sources.add(peer[0])
                if busy:
                    conn.close()
                    continue
                Thread(target=self._handle, args=(conn, peer[0], on_pair), daemon=True).start()
        except OSError:
            pass
        finally:
            self.close()

    def _handle(self, conn, peer, on_pair):
        """One thread per connection so idle clients cannot hold up the real iPhone."""
        try:
            with conn:
                device = self._exchange(conn, peer)
                if not device:
                    return
                with self._lock:
                    if self._used or self._stop.is_set() or time.monotonic() >= self._expires:
                        return
                    self._used = True
                on_pair(device)
                try:
                    conn.sendall(self._reply)
                except OSError:
                    pass
                self.close()
        finally:
            with self._lock:
                self._active -= 1
                self._sources.discard(peer)

    def _exchange(self, conn, peer):
        """ハンドシェイク (期限 HS_SECONDS)。成功したら Device、だめなら None。"""
        deadline = min(self._expires, time.monotonic() + HS_SECONDS)
        responder = QrResponder(self.identity, self._token)
        try:
            conn.settimeout(0.25)
            msg_type, q1, _hs = read_hs(conn, self._stop, deadline)
            if msg_type != MSG_CONTROL:
                return None
            q2 = responder.on_q1(q1)
            conn.sendall(encode_msg(MSG_CONTROL, q2) + encode_msg(MSG_CONTROL, responder.confirm_message()))
            msg_type, ct = read_msg(conn, self._stop, False, None, deadline)
            if msg_type != MSG_SECURE:
                return None
            channel = responder.channel()
            inner_type, body = channel.open(ct)
            hs = hs_parse(body) if inner_type == MSG_CONTROL else None
            if hs is None or not responder.check_token(hs.get("tok")):
                return None
            info = parse_peer(hs)
            self._reply = responder.reply_frame(channel, self.pc_name)
        except (OSError, ValueError, RecursionError, PhoneCamError, ConnectionError):
            return None
        addresses = ipv4([peer, *info["addresses"]])
        return Device(info["id"], info["name"], addresses[:32], "", info["model"], "", info["s"].hex())


def local_ipv4():
    """この PC の IPv4 アドレス (Qt なしで求める。自動接続の Wi-Fi 検索用)。"""
    found = []
    try:
        found.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 9))   # 送信はしない。経路の選択だけで自分のアドレスが分かる
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    return ipv4(found)


def route_of(address):
    """見つかった経路: "tether" (iPhone のテザリング) / "hotspot" (PC のホットスポット) / "lan" (同じ Wi-Fi)。"""
    if address == TETHER:
        return "tether"
    try:
        if ipaddress.IPv4Address(address) in HOTSPOT:
            return "hotspot"
    except ValueError:
        pass
    return "lan"


def on_tether(addresses):
    """PC が iPhone のテザリング (172.20.10.0/28) につながっているか。"""
    net = ipaddress.IPv4Network("172.20.10.0/28")
    return any(_in(net, a) for a in addresses)


def hotspot_on(addresses):
    """PC のモバイルホットスポットがオンか (192.168.137.1 を持っている)。"""
    return "192.168.137.1" in addresses


def _in(net, address):
    try:
        return ipaddress.IPv4Address(address) in net
    except ValueError:
        return False


def _port_open(ip):
    try:
        with socket.create_connection((ip, 5555), timeout=0.16):
            return True
    except OSError:
        return False


def _default_tether_gateway():
    try:
        routes = subprocess.check_output(["route.exe", "print", "-4"], text=True, errors="replace", timeout=3,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return any(row.split()[:3] == ["0.0.0.0", "0.0.0.0", TETHER] and len(row.split()) == 5
               for row in routes.splitlines())


def _bonjour(interfaces):
    from zeroconf import IPVersion, ServiceBrowser, Zeroconf

    found = {}
    lock = Lock()

    def changed(zeroconf, service_type, name, state_change):
        with lock:
            found[name] = state_change

    with Zeroconf(interfaces=list(interfaces), ip_version=IPVersion.V4Only) as zc:
        browser = ServiceBrowser(zc, SERVICE, handlers=[changed])
        try:
            time.sleep(1.5)
            devices = []
            with lock:
                services = list(found.items())[:32]
            for name, state in services:
                if state.name == "Removed":
                    continue
                info = zc.get_service_info(SERVICE, name, timeout=350)
                if not info or info.port != 5555 or info.properties.get(b"v") not in (b"1", b"2"):
                    continue
                ident = device_id((info.properties.get(b"id") or b"").decode("ascii", errors="replace"))
                addresses = ipv4(info.parsed_addresses())
                label = (info.properties.get(b"name") or b"").decode("utf-8", errors="replace")
                model = (info.properties.get(b"model") or b"").decode("utf-8", errors="replace")
                if ident and addresses:
                    devices.append(Device(ident, label.strip()[:128], addresses, "", model.strip()[:128]))
            return devices
        finally:
            browser.cancel()


def discover(interfaces):
    """Discover without blocking the Qt thread; subnet probes require a local hotspot address."""
    if not interfaces:
        return []
    probes = []
    if _default_tether_gateway():
        probes.append(TETHER)
    if "192.168.137.1" in interfaces:
        probes.extend(str(ip) for ip in HOTSPOT.hosts() if str(ip) not in interfaces)
    with ThreadPoolExecutor(max_workers=24) as pool:
        bonjour = pool.submit(_bonjour, interfaces)
        checked = [(ip, pool.submit(_port_open, ip)) for ip in probes]
        try:
            devices = bonjour.result()
        except (OSError, ImportError):
            devices = []
        used = {ip for device in devices for ip in device.addresses}
        devices.extend(Device("", "", (ip,)) for ip, result in checked if ip not in used and result.result())
    return devices


def merge_devices(known, discovered):
    """Keep the pinned key for a known ID; if found anew, prefer the advertised addresses."""
    by_id = {device.id: device for device in discovered if device.id}
    result = []
    ids, ips = set(), set()
    for device in known:
        fresh = by_id.get(device.id)
        item = (Device(device.id, fresh.name or device.name, fresh.addresses or device.addresses, device.code,
                       fresh.model or device.model, device.alias, device.key, device.repair) if fresh else device)
        result.append(item)
        ids.add(item.id)
        ips.update(item.addresses)
    for item in discovered:
        if item.id not in ids and (item.id or not ips.intersection(item.addresses)):
            result.append(item)
            if item.id:
                ids.add(item.id)
            ips.update(item.addresses)
    return result
