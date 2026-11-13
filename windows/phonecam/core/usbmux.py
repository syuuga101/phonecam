# -*- coding: utf-8 -*-
"""usbmuxd クライアント (USB 経由) と Wi-Fi 接続
"""

import plistlib
import socket
import struct
from phonecam.core.protocol import PHONE_PORT, PhoneCamError, USBMUX_ADDR, read_exact


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


def usbmux_connect(port=PHONE_PORT, mux_addr=USBMUX_ADDR, timeout=5.0, device_id=None, info=None):
    """usbmux 経由で USB 接続した iPhone のポートへ接続し、トンネル化したソケットを返す。
    device_id を渡すとそのデバイスへ接続する (音声チャンネルを映像と同じ iPhone に繋ぐため)。
    info (dict) には接続した device_id を入れる。"""
    try:
        sock = socket.create_connection(mux_addr, timeout=timeout)
    except OSError:
        raise PhoneCamError(
            "Apple Mobile Device Service に接続できません。"
            "Apple Devicesアプリ（またはiTunes）をインストールしてください"
        )
    try:
        sock.settimeout(timeout)
        base = {"ClientVersionString": "phonecam", "ProgName": "phonecam"}

        req = dict(base)
        req["MessageType"] = "ListDevices"
        sock.sendall(usbmux_pack(req, 1))
        resp = usbmux_recv(sock)
        devices = [d for d in resp.get("DeviceList", [])
                   if d.get("Properties", {}).get("ConnectionType") == "USB"]
        if not devices:
            raise PhoneCamError(
                "iPhoneがUSBで見つかりません。ケーブルとロック解除・このコンピュータを信頼を確認"
            )
        ids = [d["DeviceID"] for d in devices]
        if device_id is None or device_id not in ids:
            if device_id is not None:
                raise PhoneCamError("iPhoneがUSBで見つかりません")
            device_id = ids[0]
        if info is not None:
            info["device_id"] = device_id

        req = dict(base)
        req["MessageType"] = "Connect"
        req["DeviceID"] = device_id
        req["PortNumber"] = socket.htons(port)
        sock.sendall(usbmux_pack(req, 2))
        resp = usbmux_recv(sock)
        number = resp.get("Number", -1)
        if number != 0:
            if number == 3:
                raise PhoneCamError(
                    "iPhoneのPhoneCamアプリに接続できません。アプリを起動して前面に表示してください"
                )
            raise PhoneCamError("USB接続に失敗しました（エラー番号 %s）" % number)
        return sock
    except PhoneCamError:
        sock.close()
        raise
    except (OSError, ConnectionError, plistlib.InvalidFileException, struct.error, ValueError):
        sock.close()
        raise PhoneCamError("USBでの通信中にエラーが発生しました。ケーブルを抜き差しして再試行してください")


def usbmux_available(mux_addr=USBMUX_ADDR):
    """iPhone が USB でつながっているか (usbmuxd に聞くだけ。つながない)。例外は出さず False。"""
    try:
        with socket.create_connection(mux_addr, timeout=1.0) as sock:
            sock.settimeout(1.0)
            sock.sendall(usbmux_pack({"MessageType": "ListDevices", "ClientVersionString": "phonecam",
                                      "ProgName": "phonecam"}, 1))
            resp = usbmux_recv(sock)
        return any(d.get("Properties", {}).get("ConnectionType") == "USB" for d in resp.get("DeviceList", []))
    except Exception:
        return False


def wifi_connect(ip, port=PHONE_PORT, timeout=3.0):
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        raise PhoneCamError(
            "%s:%d に接続できません。IPアドレスと、iPhoneでアプリが起動していることを確認してください" % (ip, port)
        )
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock
