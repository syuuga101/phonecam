"""Independent camera controllers and shared device ownership for Qt."""

from threading import RLock
import time

from phonecam.core.profiling import PROFILE
from phonecam.core.settings import DEFAULT_SETTINGS, sanitize_settings
from phonecam.core.softcam_reg import CAMERA_SLOTS
from phonecam.core.usbmux import usbmux_connect, usbmux_devices
from phonecam.core.wireless import device_id, read_device
from phonecam.i18n import t


class CallbackList:
    """Qt を使わない簡易シグナル (controller.CallbackList と同じ振る舞い)。"""

    def __init__(self):
        self._cbs = []

    def connect(self, fn):
        if fn not in self._cbs:
            self._cbs.append(fn)
        return fn

    def emit(self, *args):
        for fn in list(self._cbs):
            try:
                fn(*args)
            except Exception:
                pass


class DeviceRoutes:
    def __init__(self, cameras):
        self.cameras = cameras
        self.lock = RLock()
        self.claims = {}
        self.identities = {}
        self.usb_cache = {}

    @property
    def multi(self):
        """カメラが 2 台以上あるときだけ、端末の固定 (USB シリアル・端末 ID) を厳しく見る。
        1 台だけなら従来どおり、つないだ USB の iPhone をそのまま使う (機種変更で詰まらない)。"""
        return len(self.cameras) > 1

    def available_usb(self, link):
        serials = usbmux_devices()
        with self.lock:
            self.usb_cache = {k: v for k, v in self.usb_cache.items() if k in serials}
            return [serial for serial in serials if self.can_claim(link, ("usb", serial))]

    def usb_identity(self, serial):
        now = time.monotonic()
        with self.lock:
            cached = self.usb_cache.get(serial)
            if cached and (cached[0] or cached[1] > now):
                return cached[0]
        try:
            with usbmux_connect(port=5557, serial=serial, timeout=.5) as sock:
                # A socket timeout here is terminal, unlike the streaming protocol's reads.
                with sock.makefile("rb") as stream:
                    ident = device_id(stream.readline(37).decode("ascii").strip())
        except Exception:
            ident = ""
        with self.lock:
            self.usb_cache[serial] = ident, now + 5
        return ident

    def unbound_device(self, link, identity):
        with self.lock:
            return all(camera.link is link or camera.get("target_device") != identity
                       for camera in self.cameras) and self.identities.get(identity, link) is link

    def unbound_usb(self, link, serial):
        identity = self.usb_identity(serial)
        with self.lock:
            if identity and not self.unbound_device(link, identity):
                return False
            return all(camera.link is link or camera.get("usb_serial") != serial for camera in self.cameras)

    def can_claim(self, link, resource):
        kind, address = resource
        with self.lock:
            if self.claims.get(resource, link) is not link:
                return False
            for camera in self.cameras:
                if camera.link is link:
                    continue
                target = camera.get("target_device")
                if kind == "usb" and camera.get("usb_serial") == address:
                    return False
                if kind == "wifi":
                    if camera.get("ip") == address:
                        return False
                    for entry in camera.get("known_devices", []):
                        device = read_device(entry)
                        if device and device.id == target and address in device.addresses:
                            return False
            return True

    def claim(self, link, resource):
        with self.lock:
            if not self.can_claim(link, resource):
                return False
            self.claims[resource] = link
            return True

    def release(self, link, resource):
        with self.lock:
            if self.claims.get(resource) is link:
                del self.claims[resource]
            self.identities = {ident: owner for ident, owner in self.identities.items() if owner is not link}

    def note_identity(self, link, value):
        identity = device_id(value)
        if not identity:
            return True    # Older iOS apps have no identity field.
        target = link.settings.get("target_device")
        with self.lock:
            if self.multi:
                if (target and target != identity) or not self.unbound_device(link, identity):
                    return False
            elif target and target != identity and link._mode != "usb":
                return False
            self.identities[identity] = link
        return True


class CameraGroup:
    """The legacy top-level settings stay on camera 1; other devices have their own profiles."""

    def __init__(self, primary):
        self.primary = primary
        self.cameras = [primary]
        self.retired = []          # 取り除いたあと、仮想カメラの登録解除が終わるまで tick だけ続けるカメラ
        self.changed = CallbackList()        # emit()  カメラの増減・割り当ての変更
        self.audio_changed = CallbackList()  # emit()  音声を受け取るカメラの変更
        self.routes = DeviceRoutes(self.cameras)
        primary.link.routes = self.routes
        primary.camera_group = self
        self.audio_index = primary.get("audio_camera", 0)
        primary.link.audio_enabled = lambda: self.audio_index == 0
        primary.status_changed.connect(lambda status: self._remember_usb(primary, status))
        primary.settings_changed.connect(self._global_changed)
        for entry in primary.get("camera_profiles", []):
            self.add(entry, save=False)
        if not 0 <= self.audio_index < len(self.cameras):
            self.audio_index = 0
            self.primary.changed("audio_camera", 0)
        if self.audio_index:
            self._apply_audio()

    def _remember_usb(self, camera, status):
        if status.connected and status.get("transport") == "usb":
            serial = camera.state.snapshot().get("usb_serial")
            if serial and serial != camera.get("usb_serial"):
                camera.changed("usb_serial", serial)
        identity = device_id(status.get("phone", {}).get("deviceId"))
        if status.connected and identity and not camera.get("target_device"):
            if any((device := read_device(entry)) and device.id == identity
                   for entry in camera.get("known_devices", [])):
                camera.changed("target_device", identity)

    def _global_changed(self, key, value):
        if key not in ("known_devices", "preview_fps", "theme"):
            return
        for camera in self.cameras[1:]:
            camera.changed(key, value)

    def add(self, profile=None, save=True):
        if len(self.cameras) >= len(CAMERA_SLOTS):
            return None
        index = len(self.cameras)
        used = {camera.get("camera_slot", 1) for camera in self.cameras}
        slot = (profile or {}).get("camera_slot")
        if slot not in CAMERA_SLOTS or slot in used:
            slot = next(number for number in CAMERA_SLOTS if number not in used)
        settings = sanitize_settings({**DEFAULT_SETTINGS, "mode_auto": True, **(profile or {})})
        settings.update(camera_slot=slot, backend="phonecam", camera_profiles=[])
        for key in ("known_devices", "theme", "language", "preview_fps"):
            settings[key] = self.primary.get(key)
        holder = []   # 作る前は camera が無いので、箱を介して「音声を受け取るカメラは自分か」を見る
        camera = type(self.primary)(settings=settings, persist=False, demo=self.primary.demo, secondary=True,
                                    audio_enabled=lambda: bool(holder) and self.cameras[self.audio_index:][:1] == holder)
        holder.append(camera)
        camera.link.routes = self.routes
        camera.camera_group = self
        self.cameras.append(camera)
        camera.status_changed.connect(lambda status: self._remember_usb(camera, status))
        camera.settings_changed.connect(lambda key, value: self._save() if key in PROFILE_KEYS else None)
        if save:
            # Preserve the existing first camera settings and backend.
            self._save()
            self.changed.emit()
        return camera

    # -- 「カメラ = OBS に出る仮想カメラ (スロット)」に iPhone を 1 台割り当てる (割り当ての唯一の窓口) ----------
    @property
    def can_add(self):
        return len(self.cameras) < len(CAMERA_SLOTS)

    @staticmethod
    def slot_of(camera):
        return camera.get("camera_slot", 1)

    def label(self, camera):
        return t("multi.camera_number", n=self.slot_of(camera))

    def obs_name(self, camera):
        from phonecam.core.softcam_reg import camera_display_name
        return camera_display_name(self.slot_of(camera))

    def users(self):
        """{iPhone の id: カメラ}。割り当て済みの iPhone だけ。"""
        return {camera.get("target_device"): camera for camera in self.cameras if camera.get("target_device")}

    def usb_users(self):
        return {camera.get("usb_serial"): camera for camera in self.cameras if camera.get("usb_serial")}

    def known_devices(self):
        return [device for entry in self.primary.get("known_devices", []) if (device := read_device(entry))]

    def phone_name(self, camera):
        """そのカメラに割り当てた iPhone の名前。接続中は iPhone が名乗る名前、未接続は登録名、無ければ「未割り当て」。"""
        identity = camera.get("target_device")
        for device in self.known_devices():
            if identity and device.id == identity:
                return device.title or t("wireless.unknown_device")
        phone = camera.status.get("phone") or {}
        live = phone.get("deviceName") or phone.get("name")
        if camera.status.get("connected") and live:
            return live
        if camera.get("usb_serial"):
            return t("multi.usb_phone")
        if camera.get("ip") or identity:
            return t("connection.device.default")
        return t("multi.unassigned")

    def camera_choice(self, camera):
        """音声の選択欄用「カメラ 1 · iPhone 16 Pro」。"""
        return t("multi.camera_choice", camera=self.label(camera), phone=self.phone_name(camera))

    def assign(self, camera, device=None, address="", usb_serial=""):
        """camera に iPhone を割り当てる。device は登録済み端末 (Device)、USB だけの端末は usb_serial で渡す。
        接続中なら一度切る。別のカメラで使用中の端末は割り当てない (False を返す)。"""
        if device is not None and device.id:
            owner = self.users().get(device.id)
            if owner is not None and owner is not camera:
                return False
        if usb_serial:
            owner = self.usb_users().get(usb_serial)
            if owner is not None and owner is not camera:
                return False
        if camera.wanted:
            camera.toggle_connect()
        if device is not None:
            camera.changed("usb_serial", usb_serial or "")
            camera.changed("target_device", device.id if device.code else "")
            camera.set_ip(address or (device.addresses[0] if device.addresses else ""))
            camera.set_pair_code(device.code)
            if camera.get("mode") == "usb":
                camera.set_mode("auto")
        else:
            camera.changed("target_device", "")
            camera.changed("usb_serial", usb_serial)
            camera.set_ip("")
            camera.set_pair_code("")
            if camera.get("mode") == "wifi":
                camera.set_mode("auto")
        self.changed.emit()
        return True

    def remove(self, camera, unregister=False):
        """2 台目以降のカメラを取り除く。unregister なら OBS 用の仮想カメラの登録も解除する (管理者権限・非同期)。"""
        if camera is self.primary or camera not in self.cameras:
            return False
        was_audio = self.cameras[self.audio_index] is camera
        before = self.cameras[self.audio_index]
        if camera.wanted:
            camera.toggle_connect()
        if unregister:
            camera.change_camera_registration("unregister")
        self.cameras.remove(camera)
        camera.camera_group = None
        self.audio_index = self.cameras.index(before) if not was_audio else 0
        if was_audio or self.audio_index != self.primary.get("audio_camera", 0):
            self.primary.changed("audio_camera", self.audio_index)
        self._save()
        if camera.camera_registration_busy:
            self.retired.append(camera)     # 登録解除の結果を受け取るため、終わるまで tick する
        else:
            camera.close()
        self._apply_audio()
        self.changed.emit()
        self.audio_changed.emit()
        return True

    def for_device(self, identity, addresses=()):
        for camera in self.cameras:
            if identity and camera.get("target_device") == identity:
                return camera
        for camera in self.cameras:
            live = camera.status.get("peer")
            if not camera.get("target_device") and (camera.get("ip") in addresses or live in addresses):
                return camera
        return None

    def bind_device(self, camera, device, address):
        owner = self.for_device(device.id, device.addresses)
        if owner is not None:
            camera = owner
        elif len(self.cameras) > 1 and camera.wanted:
            idle = next((item for item in self.cameras if not item.wanted and not item.get("target_device")
                         and not item.get("usb_serial") and not item.get("ip")), None)
            if idle is None:
                return None
            camera = idle
        if camera.wanted:
            camera.toggle_connect()
        if camera.get("target_device") != device.id or not device.id:
            camera.changed("usb_serial", "")
        camera.changed("target_device", device.id if device.code else "")
        camera.set_ip(address)
        camera.set_pair_code(device.code)
        if camera.get("mode") == "usb":
            camera.set_mode("auto")
        self.changed.emit()
        return camera

    def _save(self):
        profiles = [{key: camera.get(key) for key in PROFILE_KEYS} for camera in self.cameras[1:]]
        self.primary.changed("camera_profiles", profiles)

    def select_audio(self, index):
        if 0 <= index < len(self.cameras) and index != self.audio_index:
            self.audio_index = index
            self.primary.changed("audio_camera", index)
            self._apply_audio()
            self.audio_changed.emit()

    def _apply_audio(self):
        # Close the old audio endpoint before opening the new one.
        for index, camera in enumerate(self.cameras):
            if index != self.audio_index:
                camera.apply_audio()
                camera.link.send_control(camera.link.controls("mic"))
        camera = self.cameras[self.audio_index]
        camera.apply_audio()
        camera.link.send_control(camera.link.controls("mic"))

    def tick(self):
        if PROFILE is not None:
            started = time.perf_counter()
        for camera in self.cameras:
            if PROFILE is not None:
                camera_started = time.perf_counter()
            camera.tick()
            if PROFILE is not None:
                PROFILE.add(camera.link.vcam.slot, "gui_camera_tick", (time.perf_counter() - camera_started) * 1000)
        for camera in list(self.retired):
            camera.tick()
            if not camera.camera_registration_busy:
                self.retired.remove(camera)
                camera.close()
        if PROFILE is not None:
            PROFILE.add(0, "gui_tick", (time.perf_counter() - started) * 1000)

    def close(self):
        for camera in reversed(self.cameras + self.retired):
            camera.close()


GLOBAL_KEYS = {
    "known_devices", "camera_profiles", "audio_camera", "theme", "language", "preview_fps",
    "update_check", "update_skip_version", "setup_done", "setup_shown_version",
    "window_maximized", "window_geometry", "tk_window_geometry",
    "audio_defaults_backup", "vbcable_restore_pending", "hotkeys_enabled", "hotkeys",
}
PROFILE_KEYS = tuple(key for key in DEFAULT_SETTINGS if key not in GLOBAL_KEYS)
