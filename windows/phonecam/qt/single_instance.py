# -*- coding: utf-8 -*-
"""二重起動の防止。すでに PhoneCam が動いていれば、あとから起動した方は何も開かずに終わり、動いている方のウィンドウを前面に出す。

QLocalServer (Windows では名前付きパイプ) を使う。最初のプロセスが待ち受け、2 つめは接続して「前面に出して」と伝える。
自動更新の再起動のときは、古いプロセスが終わるまで少し待つ (PHONECAM_RELAUNCH=1)。
"""

import getpass
import os
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

RELAUNCH_ENV = "PHONECAM_RELAUNCH"
RELAUNCH_WAIT_SEC = 10.0
MESSAGE = b"show\n"
ACK = b"ok\n"


def server_name():
    """ユーザーごとの名前 (同じ PC の別ユーザー・別セッションと衝突させない)。"""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    safe = "".join(c if c.isalnum() else "_" for c in user)[:40] or "user"
    return "PhoneCamReceiver-" + safe


def _allow_foreground():
    """動いている側のウィンドウが前面に出られるよう、こちらの前面にする権利を渡す (Windows)。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)   # ASFW_ANY
    except Exception:
        pass


def notify_running(name, timeout_ms=500, send=True):
    """動いているプロセスへ「前面に出して」と伝える (send=False ならつなぐだけ)。つながれば True (= すでに起動している)。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    if send:
        _allow_foreground()
        socket.write(MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.waitForReadyRead(timeout_ms)   # 動いている側の「受け取りました」を待つ (待たずに閉じると、受け取る前に切れることがある)
    socket.disconnectFromServer()
    return True


class InstanceGuard(QObject):
    """最初のプロセスが持つ待ち受け。2 つめが来ると activated を出す。"""

    activated = Signal()

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self._clients = []
        self.name = name

    def listen(self):
        if self._server.listen(self.name):
            return True
        QLocalServer.removeServer(self.name)   # 異常終了で残った古い待ち受けを消してやり直す (Windows では通常は不要)
        return self._server.listen(self.name)

    def _on_connection(self):
        while self._server.hasPendingConnections():
            client = self._server.nextPendingConnection()
            self._clients.append(client)
            client.readyRead.connect(lambda c=client: self._on_ready(c))
            client.disconnected.connect(lambda c=client: self._drop(c))
            if client.bytesAvailable():
                self._on_ready(client)

    def _on_ready(self, client):
        try:
            data = bytes(client.readAll())
            if MESSAGE.strip() not in data:
                return
            client.write(ACK)
            client.flush()
        except RuntimeError:
            return
        self.activated.emit()

    def _drop(self, client):
        if client in self._clients:
            self._clients.remove(client)
        client.deleteLater()

    def close(self):
        self._server.close()


def claim(name=None, relaunch_wait=None):
    """このプロセスが最初なら、待ち受けを持つ InstanceGuard を返す。すでに動いていれば、そちらへ伝えて None を返す
    (呼んだ側は何も開かずに終わること)。待ち受けを作れないとき (権限など) は、起動を止めずに、守りなしの InstanceGuard を返す。"""
    name = name or server_name()
    if relaunch_wait is None:
        relaunch_wait = RELAUNCH_WAIT_SEC if os.environ.pop(RELAUNCH_ENV, "") == "1" else 0.0
    deadline = time.monotonic() + relaunch_wait
    while True:
        if not notify_running(name, send=relaunch_wait <= 0):
            break
        if time.monotonic() >= deadline:
            if relaunch_wait > 0:
                notify_running(name)
            return None
        time.sleep(0.25)
    guard = InstanceGuard(name)
    guard.listen()
    return guard
