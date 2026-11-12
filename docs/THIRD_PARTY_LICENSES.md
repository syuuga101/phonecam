# サードパーティのライセンス

PhoneCam 自身は [GPL-2.0以降（GPL-2.0-or-later）](../LICENSE) で公開しています。`PhoneCamReceiver.exe` には、次のソフトウェアが含まれます（PyInstaller で 1 つにまとめています）。

| 部品 | 用途 | ライセンス | 配布元 |
|---|---|---|---|
| softcam | 専用カメラ「PhoneCam」（DirectShow 仮想カメラ）。**改変あり**（表示名の変更、独自の CLSID・共有メモリ名への書き換え） | MIT | https://github.com/tshino/softcam |
| PyAV（`av`） | 映像のデコード | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg（PyAV のバイナリ wheel に同梱） | PyAV が使うコーデックなどのライブラリ | LGPL-2.1 以降（※1） | https://ffmpeg.org/legal.html |
| NumPy | 映像の変換 | BSD-3-Clause（同梱ライブラリに 0BSD / MIT / Zlib / CC0-1.0 を含む） | https://numpy.org/ |
| pyvirtualcam | OBS Virtual Camera / Unity Capture への出力 | GPL-2.0（※2） | https://github.com/letmaik/pyvirtualcam |
| sounddevice | 音声の再生 | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio（sounddevice の Windows 向け wheel に同梱） | 音声 I/O | MIT 系（PortAudio License） | https://portaudio.com/license.html |
| Python ランタイム | 実行環境 | PSF License | https://docs.python.org/3/license.html |
| PyInstaller のブートローダー | exe の起動部分 | GPL-2.0 + ブートローダー例外（生成した exe のライセンスは自由に選べる） | https://pyinstaller.org/en/stable/license.html |

ほかに、iPhone アプリは Apple の SDK（AVFoundation、VideoToolbox など）を使っています。

## 注記

- ※1 FFmpeg のライセンスは、wheel のビルド構成（LGPL か GPL か）によって変わります。PyAV の PyPI ページには「FFmpeg 同梱」としか書かれていないため、使用している wheel の構成は未確認です。
- ※2 pyvirtualcam は GPL-2.0 ですが、「GPL-2.0 のみ」か「GPL-2.0 以降」かは、配布元のページ（PyPI・GitHub）の表記だけでは確認できませんでした。どちらの場合でも、PhoneCam は GPL-2.0以降 で公開しているため、配布物全体を GPL-2.0 の条件で扱えるように組み合わせの条件を満たしています。
- 各ライセンスの全文は、それぞれの配布元を見てください。
- 情報は 2026 年 11 月時点で確認したものです。依存ライブラリを更新したときは、この表も見直してください。
