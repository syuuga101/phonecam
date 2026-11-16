# -*- coding: utf-8 -*-
"""usbmuxd クライアント (USB 経由) と Wi-Fi 接続
"""

import plistlib
import socket
import struct
import time
from phonecam.core.crashlog import log_error
from phonecam.core.protocol import (
    MAX_USBMUX_LENGTH, PHONE_PORT, USBMUX_ADDR, USBMUX_READ_SEC, PayloadTooLarge, PhoneCamError, read_exact)
from phonecam.i18n import t


# ---------------------------------------------------------------------------
# usbmux
# ---------------------------------------------------------------------------

class UsbError(PhoneCamError):
    """USB でつなげなかった理由の種類つき。kind: "app" (アプリ側が待ち受けていない) / "trust" (ほかの失敗。信頼やロックの確認が要る)"""

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def usbmux_pack(payload_dict, tag):
    body = plistlib.dumps(payload_dict, fmt=plistlib.FMT_XML)
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body


def usbmux_recv(sock, timeout=None):
    """usbmux の 1 メッセージを読む。宣言された長さが MAX_USBMUX_LENGTH を超えるときは本体を読まずに切る。
    1 メッセージを timeout 秒 (省略時はソケットのタイムアウト、無ければ USBMUX_READ_SEC) で読み切れなければ ReceiveTimeout。"""
    if timeout is None:
        timeout = sock.gettimeout() or USBMUX_READ_SEC
    deadline = time.monotonic() + timeout
    header = read_exact(sock, 16, idle=timeout, deadline=deadline, what="usbmux ヘッダ")
    length, _version, _msg, _tag = struct.unpack("<IIII", header)
    if length < 16 or length > MAX_USBMUX_LENGTH:
        err = PayloadTooLarge("usbmux", length, MAX_USBMUX_LENGTH)
        log_error("usbmux", err.log_line())
        raise err
    body = read_exact(sock, length - 16, idle=timeout, deadline=deadline, what="usbmux 本体") if length > 16 else b""
    return plistlib.loads(body)


def usbmux_connect(port=PHONE_PORT, mux_addr=USBMUX_ADDR, timeout=5.0, device_id=None, info=None, serial=None):
    """usbmux 経由で USB 接続した iPhone のポートへ接続し、トンネル化したソケットを返す。
    device_id を渡すとそのデバイスへ接続する (音声チャンネルを映像と同じ iPhone に繋ぐため)。
    info (dict) には接続した device_id を入れる。"""
    try:
        sock = socket.create_connection(mux_addr, timeout=timeout)
    except OSError:
        raise PhoneCamError(t("core.usb.no_service"))
    try:
        sock.settimeout(timeout)
        base = {"ClientVersionString": "phonecam", "ProgName": "phonecam"}

        req = dict(base)
        req["MessageType"] = "ListDevices"
        sock.sendall(usbmux_pack(req, 1))
        resp = usbmux_recv(sock)
        devices = [d for d in resp.get("DeviceList", [])
                   if d.get("Properties", {}).get("ConnectionType") == "USB"]
        if serial is not None:
            devices = [d for d in devices if d.get("Properties", {}).get("SerialNumber") == serial]
        if not devices:
            raise PhoneCamError(t("core.link.no_usb"))
        ids = [d["DeviceID"] for d in devices]
        if device_id is None or device_id not in ids:
            if device_id is not None:
                raise PhoneCamError(t("core.usb.not_found"))
            device_id = ids[0]
        if info is not None:
            info["device_id"] = device_id
            info["usb_serial"] = next(d.get("Properties", {}).get("SerialNumber", "")
                                      for d in devices if d["DeviceID"] == device_id)

        req = dict(base)
        req["MessageType"] = "Connect"
        req["DeviceID"] = device_id
        req["PortNumber"] = socket.htons(port)
        sock.sendall(usbmux_pack(req, 2))
        resp = usbmux_recv(sock)
        number = resp.get("Number", -1)
        if number != 0:
            if number == 3:
                raise UsbError(t("core.usb.app_closed"), "app")      # つながりを断られた: アプリが起動していない/前面にない
            raise UsbError(t("core.usb.failed", number=number), "trust")   # それ以外: 「信頼」前・ロック中など
        return sock
    except PhoneCamError:
        sock.close()
        raise
    except (OSError, ConnectionError, plistlib.InvalidFileException, struct.error, ValueError):
        sock.close()
        raise UsbError(t("core.usb.io_error"), "trust")


def usbmux_devices(mux_addr=USBMUX_ADDR):
    """Return the USB serials of attached iPhones without opening an app connection."""
    try:
        with socket.create_connection(mux_addr, timeout=1.0) as sock:
            sock.settimeout(1.0)
            sock.sendall(usbmux_pack({"MessageType": "ListDevices", "ClientVersionString": "phonecam",
                                      "ProgName": "phonecam"}, 1))
            resp = usbmux_recv(sock)
        return [props["SerialNumber"] for d in resp.get("DeviceList", [])
                if (props := d.get("Properties", {})).get("ConnectionType") == "USB" and props.get("SerialNumber")]
    except Exception:
        return []


IDENTITY_PORT = 5557


def usbmux_probe(serial, mux_addr=USBMUX_ADDR, timeout=1.5):
    """USB の iPhone の「到達確認」。映像のポート (5555) には触れず、読み取り専用の識別ポート (5557) だけにつなぎ、
    iPhone アプリの端末 ID を返す (古いアプリで ID が読めなければ "")。
    iPhone は映像の接続を 1 本しか受けず、新しい接続が来ると今の接続 (Wi-Fi) を切って乗り換えてしまう。
    そのため Wi-Fi 中に 5555 を試しにつなぐことはできない。つなげないときは PhoneCamError (usbmux_connect と同じ文言)。"""
    sock = usbmux_connect(port=IDENTITY_PORT, mux_addr=mux_addr, timeout=timeout, serial=serial)
    try:
        sock.settimeout(timeout)
        with sock.makefile("rb") as stream:
            return stream.readline(37).decode("ascii", "replace").strip()
    except (OSError, ConnectionError):
        return ""
    finally:
        sock.close()


def usbmux_app_state(serial, mux_addr=USBMUX_ADDR, timeout=0.8):
    """USB の iPhone で PhoneCam アプリが応答しているかを見る。識別ポート (5557) にだけつなぐ (映像の 5555 には触れない)。
    -> ("ready", 端末 ID の行。読めなければ "") / ("cable", "") (ケーブルは見えるが、アプリが応答していない)"""
    try:
        sock = usbmux_connect(port=IDENTITY_PORT, mux_addr=mux_addr, timeout=timeout, serial=serial)
    except PhoneCamError:
        return "cable", ""
    try:
        sock.settimeout(timeout)
        with sock.makefile("rb") as stream:
            return "ready", stream.readline(37).decode("ascii", "replace").strip()
    except (OSError, ConnectionError):
        return "ready", ""
    finally:
        sock.close()


def usbmux_available(mux_addr=USBMUX_ADDR):
    return bool(usbmux_devices(mux_addr))


def wifi_connect(ip, port=PHONE_PORT, timeout=3.0):
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        raise PhoneCamError(t("core.usb.tcp_failed", ip=ip, port=port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock
