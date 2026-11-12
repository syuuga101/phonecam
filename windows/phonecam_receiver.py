# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ (起動用の入口)

iPhone (PhoneCam アプリ) から H.264 / HEVC 映像を受信してデコードし、
仮想カメラ (PhoneCam 専用カメラ / OBS Virtual Camera / Unity Capture) へ出力します。
本体は phonecam パッケージ (core/ = ロジック、ui/ = 画面)。このファイルは
`--register` / `--unregister` / `--setup` のヘルパ起動と exe 化 (PyInstaller) の入口を担う。
"""

import sys
import types

import phonecam.core.audio_engine as _m_audio_engine
import phonecam.core.canvas as _m_canvas
import phonecam.core.decoder as _m_decoder
import phonecam.core.deps as _m_deps
import phonecam.core.elevate as _m_elevate
import phonecam.core.link as _m_link
import phonecam.core.paths as _m_paths
import phonecam.core.protocol as _m_protocol
import phonecam.core.settings as _m_settings
import phonecam.core.softcam_reg as _m_softcam_reg
import phonecam.core.softcam_shmem as _m_softcam_shmem
import phonecam.core.state as _m_state
import phonecam.core.stats as _m_stats
import phonecam.core.stream as _m_stream
import phonecam.core.usbmux as _m_usbmux
import phonecam.core.vcam as _m_vcam
import phonecam.core.version as _m_version
import phonecam.ui.app as _m_app
import phonecam.ui.dpi as _m_dpi
import phonecam.ui.labels as _m_labels
import phonecam.ui.icons as _m_icons
import phonecam.ui.theme as _m_theme
import phonecam.ui.tkdeps as _m_tkdeps
import phonecam.ui.widgets as _m_widgets
import phonecam.ui.util as _m_util
import phonecam.ui.dshow_ui as _m_dshow_ui
import phonecam.ui.phone_sync as _m_phone_sync
import phonecam.ui.update_ui as _m_update_ui
import phonecam.ui.pages.audio as _m_p_audio
import phonecam.ui.pages.connection_video as _m_p_conn
import phonecam.ui.pages.effects as _m_p_effects
import phonecam.ui.pages.quality as _m_p_quality
import phonecam.ui.pages.recording as _m_p_recording
import phonecam.ui.pages.preview as _m_p_preview
import phonecam.ui.pages.settings as _m_p_settings
import phonecam.ui.pages.setup as _m_p_setup
import phonecam.ui.pages.shortcuts as _m_p_shortcuts
import phonecam.ui.pages.status as _m_p_status
from phonecam.ui.app import App, main, _show_fatal

# 後方互換: 旧 phonecam_receiver の公開名 (テストや他モジュールが R.xxx で参照する) を再公開する。
# 値を R.xxx = ... で差し替えたときは、その名前を持つ phonecam 内の全モジュールへ伝える
# (旧 1 ファイル構成と同じ効き方にするため)。
_SUBMODULES = [v for k, v in list(globals().items()) if k.startswith("_m_")]
for _m in _SUBMODULES:
    for _k, _v in vars(_m).items():
        if not _k.startswith("__") and _k not in globals():
            globals()[_k] = _v


class _CompatModule(types.ModuleType):
    def __setattr__(self, name, value):
        types.ModuleType.__setattr__(self, name, value)
        for m in _SUBMODULES:
            if name in m.__dict__:
                m.__dict__[name] = value


sys.modules[__name__].__class__ = _CompatModule


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        _show_fatal(e)
        code = 1
    sys.exit(code)
