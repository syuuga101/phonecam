"""LAN pairing and discovery. The existing stream protocol still authenticates the PIN."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import json
import re
import secrets
import socket
import subprocess
from threading import Event, Lock, Thread
import time
from urllib.parse import urlencode
from uuid import UUID

VERSION = 1
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
        if not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
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
    code: str = ""

    def stored(self):
        return {"id": self.id, "name": self.name, "addresses": list(self.addresses), "code": self.code}


def read_device(data):
    if not isinstance(data, dict):
        return None
    ident = device_id(data.get("id"))
    addresses = ipv4(data.get("addresses"))
    name, code = data.get("name"), data.get("code")
    if not (ident and addresses and isinstance(name, str) and isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code)):
        return None
    return Device(ident, name.strip()[:128], addresses[:32], code)


class Pairing:
    """A single-use, short-lived TCP listener owned by the QR dialog."""

    def __init__(self, addresses, on_pair, seconds=PAIR_SECONDS):
        if not addresses:
            raise ValueError("No IPv4 addresses")
        self._stop = Event()
        self._expires = time.monotonic() + seconds
        self._token = secrets.token_urlsafe(24)
        self._listener = socket.socket()
        try:
            self._listener.bind(("0.0.0.0", 0))
            self._listener.listen(4)
            self._listener.settimeout(0.25)
            self.url = "phonecam://pair?" + urlencode({
                "ips": ",".join(addresses), "port": self._listener.getsockname()[1],
                "token": self._token, "name": socket.gethostname()[:128], "v": VERSION,
            })
            Thread(target=self._serve, args=(on_pair,), daemon=True).start()
        except BaseException:
            self.close()
            raise

    def close(self):
        self._stop.set()
        self._listener.close()

    def _serve(self, on_pair):
        try:
            while not self._stop.is_set() and time.monotonic() < self._expires:
                try:
                    conn, peer = self._listener.accept()
                except socket.timeout:
                    continue
                with conn:
                    device = self._read(conn, peer[0])
                    if device and not self._stop.is_set() and time.monotonic() < self._expires:
                        on_pair(device)
                        try:
                            conn.sendall(b'{"ok":true}\n')
                        except OSError:
                            pass
                        return
        except OSError:
            pass
        finally:
            self.close()

    def _read(self, conn, peer):
        deadline = min(self._expires, time.monotonic() + 2)
        buf = bytearray()
        try:
            while len(buf) < 4096 and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                conn.settimeout(min(remaining, 0.25))
                try:
                    chunk = conn.recv(min(1024, 4096 - len(buf)))
                except socket.timeout:
                    continue
                if not chunk:
                    return None
                buf.extend(chunk)
                if b"\n" in buf:
                    break
            if b"\n" not in buf:
                return None
            data = json.loads(buf.split(b"\n", 1)[0])
        except (OSError, ValueError, RecursionError):
            return None
        if not isinstance(data, dict):
            return None
        token = data.get("token")
        if not isinstance(token, str) or not secrets.compare_digest(token.encode("utf-8"), self._token.encode("ascii")):
            return None
        if data.get("v") != VERSION:
            return None
        if isinstance(data.get("addresses"), list):
            data["addresses"] = [peer, *data["addresses"]]
        return read_device(data)


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
                if not info or info.port != 5555 or info.properties.get(b"v") != b"1":
                    continue
                ident = device_id((info.properties.get(b"id") or b"").decode("ascii", errors="replace"))
                addresses = ipv4(info.parsed_addresses())
                label = (info.properties.get(b"name") or b"").decode("utf-8", errors="replace")
                if ident and addresses:
                    devices.append(Device(ident, label[:128], addresses))
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
    """Keep the PIN for a known ID; if found anew, prefer the advertised addresses."""
    by_id = {device.id: device for device in discovered if device.id}
    result = []
    ids, ips = set(), set()
    for device in known:
        fresh = by_id.get(device.id)
        item = Device(device.id, fresh.name or device.name, fresh.addresses, device.code) if fresh else device
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
