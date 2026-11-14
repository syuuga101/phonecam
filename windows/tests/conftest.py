import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def known_iphone():
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
