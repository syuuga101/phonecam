"""Independent camera controllers and shared device ownership for Qt."""

from threading import RLock
import time

from phonecam.core.protocol import read_exact
from phonecam.core.settings import DEFAULT_SETTINGS, sanitize_settings
from phonecam.core.softcam_reg import CAMERA_SLOTS
from phonecam.core.usbmux import usbmux_connect, usbmux_devices
from phonecam.core.wireless import device_id, read_device


class DeviceRoutes:
    def __init__(self, cameras):
        self.cameras = cameras
        self.lock = RLock()
        self.claims = {}
        self.identities = {}
        self.usb_cache = {}

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
                ident = device_id(read_exact(sock, 37).decode("ascii").strip())
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
            if (target and target != identity) or not self.unbound_device(link, identity):
                return False
            self.identities[identity] = link
        return True


class CameraGroup:
    """The legacy top-level settings stay on camera 1; other devices have their own profiles."""

    def __init__(self, primary):
        self.primary = primary
        self.cameras = [primary]
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
        camera = type(self.primary)(settings=settings, persist=False, demo=self.primary.demo,
                                    secondary=True, audio_enabled=lambda: self.audio_index == index)
        camera.link.routes = self.routes
        camera.camera_group = self
        self.cameras.append(camera)
        camera.status_changed.connect(lambda status: self._remember_usb(camera, status))
        camera.settings_changed.connect(lambda key, value: self._save() if key in PROFILE_KEYS else None)
        if save:
            # Preserve the existing first camera settings and backend.
            self._save()
        return camera

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
        return camera

    def _save(self):
        profiles = [{key: camera.get(key) for key in PROFILE_KEYS} for camera in self.cameras[1:]]
        self.primary.changed("camera_profiles", profiles)

    def move_output(self, camera, slot):
        if slot not in CAMERA_SLOTS or camera.camera_registration_busy:
            return False
        other = next((item for item in self.cameras
                      if item is not camera and item.get("camera_slot") == slot), None)
        if other is None:
            camera.set_camera_slot(slot)
            return True
        if other.camera_registration_busy:
            return False
        # Both senders must be closed before either can claim the other's memory.
        previous_slot = camera.get("camera_slot")
        with camera.link.vcam._lock, other.link.vcam._lock:
            camera.link.vcam.close()
            other.link.vcam.close()
            camera.set_camera_slot(slot)
            other.set_camera_slot(previous_slot)
        return True

    def select_audio(self, index):
        if 0 <= index < len(self.cameras) and index != self.audio_index:
            self.audio_index = index
            self.primary.changed("audio_camera", index)
            self._apply_audio()

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
        for camera in self.cameras:
            camera.tick()

    def close(self):
        for camera in reversed(self.cameras):
            camera.close()


GLOBAL_KEYS = {
    "known_devices", "camera_profiles", "audio_camera", "theme", "language", "preview_fps",
    "update_check", "update_skip_version", "setup_done", "setup_shown_version",
    "window_maximized", "window_geometry", "tk_window_geometry",
    "audio_defaults_backup", "vbcable_restore_pending", "hotkeys_enabled", "hotkeys",
}
PROFILE_KEYS = tuple(key for key in DEFAULT_SETTINGS if key not in GLOBAL_KEYS)
