# -*- coding: utf-8 -*-
"""usbmuxd クライアント (USB 経由) と Wi-Fi 接続
"""

import plistlib
import socket
import struct
from phonecam.core.protocol import PHONE_PORT, PhoneCamError, USBMUX_ADDR, read_exact
from phonecam.i18n import t


# ---------------------------------------------------------------------------
# usbmux
# ---------------------------------------------------------------------------

def usbmux_pack(payload_dict, tag):
    body = plistlib.dumps(payload_dict, fmt=plistlib.FMT_XML)
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body


def usbmux_recv(sock):
    header = read_exact(sock, 16)
    length, _version, _msg, _tag = struct.unpack("<IIII", header)
    body = read_exact(sock, length - 16) if length > 16 else b""
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
                raise PhoneCamError(t("core.usb.app_closed"))
            raise PhoneCamError(t("core.usb.failed", number=number))
        return sock
    except PhoneCamError:
        sock.close()
        raise
    except (OSError, ConnectionError, plistlib.InvalidFileException, struct.error, ValueError):
        sock.close()
        raise PhoneCamError(t("core.usb.io_error"))


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


def usbmux_available(mux_addr=USBMUX_ADDR):
    return bool(usbmux_devices(mux_addr))


def wifi_connect(ip, port=PHONE_PORT, timeout=3.0):
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        raise PhoneCamError(t("core.usb.tcp_failed", ip=ip, port=port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock
