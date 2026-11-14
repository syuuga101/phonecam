import numpy as np

from phonecam import controller as receiver
from phonecam.core import canvas, settings
from phonecam.core.audio_engine import AudioEngine
from phonecam.labels import phone_mic_text
from phonecam.i18n import t


def _engine(params):
    engine = AudioEngine(lambda: params["p"])
    engine.setup(48000)
    return engine


def test_pc_mute_outputs_silence_but_keeps_stream_running():
    params = {"p": (100, True, 0)}
    engine = _engine(params)
    tone = (0.5 * np.sin(np.linspace(0, 100, 4800))).astype(np.float32)
    engine.push(tone)
    out = engine.pull(480)
    assert out.shape == (480,) and not out.any()          # 無音を流し続ける (止めない)
    assert engine.buffered_ms() > 0                       # 受信は続いていて、バッファは進む
    params["p"] = (100, False, 0)                          # 解除するとすぐ音が出る
    assert np.abs(engine.pull(480)).max() > 0


def test_no_mic_muted_in_any_control_group():
    config = {**settings.DEFAULT_SETTINGS, "mic_mute": True, "mic_muted": True}
    for group in ("all", "mic", "micMuted"):
        assert "micMuted" not in canvas.build_control(config, group)


def test_hotkey_toggles_pc_mute_without_sending(monkeypatch):
    ctrl = receiver.Controller(persist=False, demo=True)
    sent = []
    monkeypatch.setattr(ctrl.link, "send_control", sent.append)
    assert ctrl._run_hotkey("mic") == t("shortcuts.toast.mic_muted")
    assert ctrl.get("mic_mute") is True and ctrl.get("mic_muted") is False
    assert ctrl._run_hotkey("mic") == t("shortcuts.toast.mic_unmuted")
    assert ctrl.get("mic_mute") is False
    ctrl._flush(float("inf"))
    assert sent == []


def test_phone_mute_is_read_only_display():
    ctrl = receiver.Controller(persist=False, demo=True)
    ctrl.state.update(connected=True, phone_status={"micMuted": True})
    status = ctrl.poll(now=100)
    assert status.phone_muted is True
    assert phone_mic_text(True, True) == t("audio.phonemic.muted")
    assert phone_mic_text(True, False) == t("audio.phonemic.on")
    assert phone_mic_text(False, True) == "-"
    assert ctrl.get("mic_mute") is False                    # PC のミュートは変わらない
