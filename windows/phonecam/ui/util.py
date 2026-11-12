# -*- coding: utf-8 -*-
"""UI 共通の小物 (webbrowser_open)
"""

def webbrowser_open(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
