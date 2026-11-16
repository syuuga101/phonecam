import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def known_iphone():
    return {
        "id": "9bd344c9-765e-4cb6-ae81-4c77f064131a",
        "name": "iPhone",
        "addresses": ["192.168.1.7"],
        "key": "7b815e67618479e7f7508c1a7dc26bcbcc24c77773f64ae0afe5d7319f764615",   # ペアリング済み (v2)
    }


@pytest.fixture
def legacy_iphone():
    """v1 で登録した端末 (鍵が無い。再ペアリングが必要)。"""
    return {
        "id": "9bd344c9-765e-4cb6-ae81-4c77f064131a",
        "name": "iPhone",
        "addresses": ["192.168.1.7"],
        "code": "012345",
    }


@pytest.fixture(autouse=True)
def test_language(monkeypatch):
    from phonecam import i18n

    monkeypatch.setattr(i18n, "_LANG", "ja")
