param(
  [ValidateSet("hide", "show", "minimize")]
  [string]$Mode = "hide"
)

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class BrowserWindowHelper {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] private static extern int GetClassName(IntPtr hWnd, StringBuilder name, int count);

  public static string ClassNameOf(IntPtr hWnd) {
    var sb = new StringBuilder(128);
    GetClassName(hWnd, sb, 128);
    return sb.ToString();
  }

  public static IntPtr[] FindWindows(int processId) {
    var found = new List<IntPtr>();
    EnumWindows((hWnd, lParam) => {
      uint pid;
      GetWindowThreadProcessId(hWnd, out pid);
      if (pid != (uint)processId) return true;
      var cls = ClassNameOf(hWnd);
      if (cls == "Chrome_WidgetWin_0" || cls == "Chrome_WidgetWin_1") found.Add(hWnd);
      return true;
    }, IntPtr.Zero);
    return found.ToArray();
  }
}
"@

$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object {
    $_.CommandLine -like "*douyin-agent*chrome-profile*" -and
    $_.CommandLine -notlike "*--type=*"
  }
if (-not $procs) {
  Write-Output "NO_DEDICATED_CHROME_PROCESS"
  exit 0
}

# SWP_NOZORDER | SWP_SHOWWINDOW
$swpFlags = 0x0004 -bor 0x0040
$count = 0
foreach ($proc in $procs) {
  foreach ($hWnd in [BrowserWindowHelper]::FindWindows([int]$proc.ProcessId)) {
    $isMain = [BrowserWindowHelper]::ClassNameOf($hWnd) -eq "Chrome_WidgetWin_1"
    if ($Mode -eq "hide") {
      [BrowserWindowHelper]::ShowWindow($hWnd, 0) | Out-Null
      $count++
    }
    elseif ($Mode -eq "minimize") {
      if ($isMain) {
        [BrowserWindowHelper]::ShowWindow($hWnd, 6) | Out-Null
        $count++
      }
    }
    elseif ($Mode -eq "show") {
      if ($isMain) {
        # 先移到屏幕内，再 SW_SHOW；SW_RESTORE 在离屏窗口上会恢复回旧位置。
        [BrowserWindowHelper]::SetWindowPos($hWnd, [IntPtr]::Zero, 80, 60, 1440, 960, 0x0004) | Out-Null
        [BrowserWindowHelper]::ShowWindow($hWnd, 5) | Out-Null
        [BrowserWindowHelper]::SetForegroundWindow($hWnd) | Out-Null
        $count++
      } else {
        [BrowserWindowHelper]::ShowWindow($hWnd, 0) | Out-Null
      }
    }
  }
}
$pids = (($procs | Select-Object -ExpandProperty ProcessId) -join ',')
Write-Output ("{0}: {1} window(s) pids={2}" -f $Mode, $count, $pids)
