import threading
from pathlib import Path
from unittest.mock import Mock

import phonecam_setup as setup


def test_guided_flow_detects_after_own_installer_exits(tmp_path, monkeypatch):
    def download(url, path, kind, *, progress, cancel):
        Path(path).write_bytes(b"fake installer")
        progress(1, 1)

    detector = Mock(return_value={"itunes": {"state": setup.STATE_MISSING}})
    installer = Mock()
    installer.alive.return_value = True
    launcher = Mock(return_value=installer)
    monkeypatch.setattr(setup, "check_installer_signature", lambda *a: (True, "", False))
    flow = setup.GuidedFlow(["itunes", "iloader"], tmp_path, downloader=download,
                            detector=detector, launcher=launcher, store_check=lambda cid: False)
    assert flow.phase == flow.PREPARING
    flow.prepare()
    assert flow.phase == flow.READY
    assert flow.launch()
    assert flow.phase == flow.RUNNING
    assert flow.check() == "running"
    installer.alive.return_value = False
    assert flow.check() == "missing"
    assert flow.phase == flow.MISSING
    detector.return_value = {"itunes": {"state": setup.STATE_OK}}
    assert flow.check() == "done"
    assert flow.current == "iloader" and flow.phase == flow.PREPARING
    assert flow.results["itunes"]["ok"]
    flow.skip()
    assert flow.finished and flow.results["iloader"]["skipped"]
    launcher.assert_called_once()
    installer.close.assert_called_once()


def test_engine_reports_manual_install_when_download_fails(tmp_path):
    detector = Mock(return_value={"vcredist": {"state": setup.STATE_MISSING}})
    download = Mock(side_effect=setup.DownloadError("download unavailable"))
    engine = setup.SetupEngine(tmp_path, downloader=download, detector=detector, winget_finder=lambda: None)
    emit = Mock()
    result = engine.run(["vcredist"], emit=emit)
    assert result["results"]["vcredist"] == {"ok": False, "detail": "download unavailable", "code": None}
    assert [entry["id"] for entry in result["manual"]] == ["vcredist"]
    assert result["statuses"] == detector.return_value
    download.assert_called_once()
    detector.assert_called_once()
    assert any(call.args == ("progress",) and call.kwargs == {"value": 1.0}
               for call in emit.call_args_list)


def test_setup_controller_moves_from_checking_to_ready():
    started = threading.Event()
    release = threading.Event()
    statuses = {"camera": {"state": setup.STATE_MISSING}}

    def detect():
        started.set()
        release.wait(5)
        return statuses

    root = Mock()
    controller = setup.SetupController(root, engine=Mock(), detector=detect)
    events = []
    worker_done = threading.Event()
    original_put = controller._q.put

    def put(result):
        original_put(result)
        worker_done.set()

    controller._q.put = put
    controller.listen(events.append)
    try:
        controller.recheck()
        assert started.wait(5)
        assert controller.checking and not controller.checked
        assert events == ["checking"]
        controller.recheck()
        root.after.assert_called_once()
    finally:
        release.set()
    assert worker_done.wait(5)
    controller.pump()
    assert controller.checked and not controller.checking
    assert controller.statuses == statuses
    assert controller.selection["camera"]
    assert events == ["checking", "statuses"]
    controller.close()
