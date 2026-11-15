# 指定したプロセス名 (onefile の exe は親と子の 2 つ) が持つ、見えているトップレベルウィンドウの一覧を出す。
# 使い方: ./count_windows.ps1 PhoneCamReceiver   → 1 行に 1 ウィンドウ ("pid|クラス名|タイトル") を標準出力へ
param([Parameter(Mandatory = $true)][string]$ProcessName)
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class PcWindows {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    public static List<string> Visible(uint[] pids) {
        var list = new List<string>();
        var set = new HashSet<uint>(pids);
        EnumWindows((h, l) => {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (set.Contains(pid) && IsWindowVisible(h)) {
                var t = new StringBuilder(256); GetWindowText(h, t, 256);
                var c = new StringBuilder(256); GetClassName(h, c, 256);
                list.Add(pid + "|" + c + "|" + t);
            }
            return true;
        }, IntPtr.Zero);
        return list;
    }
}
"@
$pids = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
if ($pids.Count -eq 0) { return }
[PcWindows]::Visible($pids) | ForEach-Object { $_ }
