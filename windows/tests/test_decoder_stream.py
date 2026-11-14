from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from phonecam.core.decoder import BacklogGuard, FrameMailbox
from phonecam.core.softcam_shmem import ConsumerWatcher
from phonecam.core.stream import StreamController


def test_mailbox_overwrites_stale_frames_and_drains_on_close():
    box = FrameMailbox()
    assert box.get(timeout=0) is None
    assert not box.put("first")
    assert box.put("latest")
    assert (box.put_count, box.skipped) == (2, 1)
    box.close()
    assert box.get() == "latest"
    assert box.get() is None


def test_mailbox_close_unblocks_reader(monkeypatch):
    box = FrameMailbox()
    waiting = Event()
    wait = box._cond.wait

    def tracked_wait(timeout):
        waiting.set()
        assert wait(timeout), "Reader timed out before the mailbox notified it"

    monkeypatch.setattr(box._cond, "wait", tracked_wait)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(box.get, 5)
        assert waiting.wait(5)
        box.close()
        assert reader.result(timeout=2) is None
    assert box.closed


def test_decode_debt_waits_for_keyframe_and_resets():
    guard = BacklogGuard(lag_ms=10)
    keyframe = Mock(return_value=False)
    guard.note_decode(12, 6)
    assert guard.decide(keyframe)
    keyframe.assert_not_called()
    guard.note_decode(12, 6)
    assert not guard.decide(keyframe)
    assert guard.lagging and guard.skipped == 1
    keyframe.return_value = True
    assert guard.decide(keyframe)
    assert not guard.lagging and guard.debt == 0


def test_communication_lag_must_be_sustained():
    guard = BacklogGuard(lag_ms=10, sustain=2)
    for ms in (20, 50, None, 20, 50):
        guard.note_arrival(ms)
        assert not guard.behind()
    guard.note_arrival(50)
    assert guard.behind()


def test_auto_stream_sends_transitions_once_and_restarts_on_use():
    stream = StreamController({"backend": "phonecam", "run_mode": "auto", "idle_stop_sec": 10})
    assert stream.on_connect(0) is False
    stream.note_in_use(True, 1)
    assert stream.pending(True, 1) is True
    stream.mark_sent(True)
    stream.note_in_use(False, 2)
    assert stream.pending(True, 10.9) is None
    assert stream.pending(True, 11) is False
    stream.mark_sent(False)
    stream.note_in_use(True, 12)
    assert stream.pending(True, 12) is True
    assert stream.pending(False, 12) is None


def test_manual_mode_ignores_camera_usage_and_idle_timeout():
    stream = StreamController({"backend": "phonecam", "run_mode": "manual", "idle_stop_sec": 10})
    stream.note_in_use(True, 1)
    assert not stream.desired(True, 1)
    stream.press_button(True, 1)
    stream.note_in_use(False, 2)
    assert stream.desired(True, 1000)
    stream.press_button(True, 1000)
    stream.note_in_use(True, 1001)
    assert not stream.desired(True, 1001)


def test_auto_pause_clears_when_a_consumer_reopens_camera():
    stream = StreamController({"backend": "phonecam", "run_mode": "auto", "idle_stop_sec": 0})
    stream.note_in_use(True, 1)
    stream.note_in_use(False, 2)
    assert stream.desired(True, 1000)
    stream.press_button(True, 1000)
    assert not stream.desired(True, 1000)
    stream.note_in_use(True, 1001)
    assert stream.desired(True, 1001)


def test_consumer_detection_uses_open_and_heartbeat_changes():
    reader = Mock()
    reader.read_info.side_effect = [
        {"hb": 255, "ver": 0}, {"hb": 255, "ver": 1},
        {"hb": 0, "ver": 1}, {"hb": 0, "ver": 1}, None,
    ]
    watcher = ConsumerWatcher(reader, window=3)
    watcher.poll(0)
    assert watcher.state(0) == "idle"
    watcher.poll(1)
    assert watcher.state(1) == "in_use"
    watcher.poll(3)
    assert watcher.state(5) == "in_use"
    watcher.poll(7)
    assert watcher.state(7) == "idle"
    watcher.poll(8)
    assert watcher.state(8) == "nomem"
    watcher.reset()
    reader.close.assert_called_once()
