# -*- coding: utf-8 -*-
"""管理者権限での再実行 (ShellExecuteEx)
"""

import ctypes
import sys


class _SHELLEXECUTEINFOW(ctypes.Structure):
    # Win32 の SHELLEXECUTEINFOW (64bit で 112 byte)。型は wintypes に頼らず固定幅で定義する
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


_SEE_MASK_NOCLOSEPROCESS = 0x00000040


_SW_HIDE = 0


_ERROR_CANCELLED = 1223


_WAIT_OBJECT_0 = 0


ELEVATED_TIMEOUT_MS = 180000


def _win_dll(name):
    return ctypes.WinDLL(name, use_last_error=True)


def run_elevated(params, timeout_ms=ELEVATED_TIMEOUT_MS):
    """自分自身を管理者権限で params 付きで起動し、終了まで待つ。
    戻り値: ("ok", 0) / ("failed", exit_code) / ("cancelled", 0) / ("timeout", 0) / ("error", win_error)。ブロックするのでワーカースレッドで呼ぶ。"""
    shell32 = _win_dll("shell32")
    kernel32 = _win_dll("kernel32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = params
    info.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        return ("cancelled", 0) if err == _ERROR_CANCELLED else ("error", err)
    if not info.hProcess:
        return ("error", 0)
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout_ms)) != _WAIT_OBJECT_0:
            return ("timeout", 0)
        code = ctypes.c_uint32(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return ("ok", 0) if code.value == 0 else ("failed", int(code.value))
    finally:
        kernel32.CloseHandle(info.hProcess)
