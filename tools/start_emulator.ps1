# 打开官方可见模拟器、安装正式 APK 并进入设置；不启动电脑服务。
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskSdk=Join-Path $taskRoot '~temp/android-sdk'
$taskAdb=Join-Path $taskSdk 'platform-tools/adb.exe'
$taskEmulator=Join-Path $taskSdk 'emulator/emulator.exe'
$taskApk=Join-Path $taskRoot 'reader.apk'
$taskLogs=Join-Path $taskRoot '~temp/logs'
$taskEmulatorOut=Join-Path $taskLogs 'emulator.out.log'
$taskEmulatorErr=Join-Path $taskLogs 'emulator.err.log'
Write-Host '正在检查 ReaderAosp35、ADB 和 APK…'
foreach ($taskFile in @($taskAdb,$taskEmulator,$taskApk)) {
    if (!(Test-Path -LiteralPath $taskFile)) {throw "缺少文件：$taskFile。请按 README 的运行环境与重建说明恢复。"}
}
$env:ANDROID_HOME=$taskSdk
$env:ANDROID_AVD_HOME=Join-Path $taskRoot '~temp/avd'
# emulator.exe 的原始诊断写入日志；当前窗口只显示启动阶段与结构化 ReaderDebug 事件。
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
# 避免安装或重启打断专用设备正在进行的自动采集。
$taskCollectors=@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match '(reproduce_test\.py|full_volume_run\.py)' -and $_.CommandLine -match '\b(collect|setup|prepare)\b'
})
if ($taskCollectors.Count) {throw '自动采集/设备准备正在运行，请等它结束或先停止测试，再打开模拟器。'}
function Get-ReaderDevices {return (& $taskAdb devices | Out-String)}
function Assert-ReaderAvd {
    $taskName=(& $taskAdb -s emulator-5554 emu avd name | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or $taskName.Trim() -ne 'ReaderAosp35') {throw '5554 端口不是可核验的 ReaderAosp35，未操作该设备。'}
}
# Windows 控制台进入“选择”模式会暂停模拟器；启动阶段检测到后自动退出。
function Resume-ReaderConsole {
    $taskProcesses=@(Get-Process emulator -ErrorAction SilentlyContinue | Where-Object {
        try {$_.Path -eq $taskEmulator} catch {$false}
    })
    foreach ($taskProcess in $taskProcesses) {
        if ($taskProcess.MainWindowTitle -match '^(选择|Select) ') {
            if (!('ReaderConsoleWindow' -as [type])) {
                Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ReaderConsoleWindow { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr key, IntPtr data); }'
            }
            [ReaderConsoleWindow]::PostMessage($taskProcess.MainWindowHandle,0x0100,[IntPtr]0x1B,[IntPtr]::Zero)|Out-Null
            [ReaderConsoleWindow]::PostMessage($taskProcess.MainWindowHandle,0x0101,[IntPtr]0x1B,[IntPtr]::Zero)|Out-Null
            Write-Host '检测到模拟器控制台处于选择模式，已恢复启动。'
        }
    }
}
function Set-ReaderEmulatorWindowVisible {
    if (!('ReaderEmulatorWindow' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ReaderEmulatorWindow {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MONITORINFO {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
    }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out RECT rect);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
    [DllImport("user32.dll")] public static extern bool GetMonitorInfo(IntPtr monitor, ref MONITORINFO info);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr window, int x, int y, int width, int height, bool repaint);
}
'@
    }
    $taskDeadline=[DateTime]::UtcNow.AddSeconds(15)
    do {
        $taskQemu=Get-CimInstance Win32_Process | Where-Object {
            $_.Name -eq 'qemu-system-x86_64.exe' -and $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($taskSdk+'\',[StringComparison]::OrdinalIgnoreCase) -and
            $_.CommandLine -match '-avd\s+ReaderAosp35\b' -and $_.CommandLine -match '-port\s+5554\b'
        } | Select-Object -First 1
        if ($taskQemu) {
            $taskWindow=Get-Process -Id $taskQemu.ProcessId -ErrorAction SilentlyContinue
            if ($taskWindow -and $taskWindow.MainWindowHandle -ne 0) {
                $taskRect=New-Object ReaderEmulatorWindow+RECT
                $taskInfo=New-Object ReaderEmulatorWindow+MONITORINFO
                $taskInfo.cbSize=[Runtime.InteropServices.Marshal]::SizeOf($taskInfo)
                $taskHandle=$taskWindow.MainWindowHandle
                $taskMonitor=[ReaderEmulatorWindow]::MonitorFromWindow($taskHandle,2)
                if (![ReaderEmulatorWindow]::GetWindowRect($taskHandle,[ref]$taskRect) -or
                    ![ReaderEmulatorWindow]::GetMonitorInfo($taskMonitor,[ref]$taskInfo)) {
                    throw '无法读取模拟器窗口或显示器工作区。'
                }
                $taskWidth=$taskRect.Right-$taskRect.Left
                $taskHeight=$taskRect.Bottom-$taskRect.Top
                $taskOriginalWidth=$taskWidth
                $taskOriginalHeight=$taskHeight
                $taskAvailableWidth=[Math]::Max(320,$taskInfo.rcWork.Right-$taskInfo.rcWork.Left-40)
                $taskAvailableHeight=[Math]::Max(480,$taskInfo.rcWork.Bottom-$taskInfo.rcWork.Top-40)
                $taskScale=[Math]::Min(1.0,[Math]::Min($taskAvailableWidth/$taskWidth,$taskAvailableHeight/$taskHeight))
                if ($taskScale -lt 1.0) {
                    $taskWidth=[Math]::Floor($taskWidth*$taskScale)
                    $taskHeight=[Math]::Floor($taskHeight*$taskScale)
                }
                $taskMinX=$taskInfo.rcWork.Left+20
                $taskMinY=$taskInfo.rcWork.Top+20
                $taskMaxX=$taskInfo.rcWork.Right-$taskWidth-20
                $taskMaxY=$taskInfo.rcWork.Bottom-$taskHeight-20
                if ($taskMaxX -lt $taskMinX) {$taskMinX=$taskInfo.rcWork.Left;$taskMaxX=$taskMinX}
                if ($taskMaxY -lt $taskMinY) {$taskMinY=$taskInfo.rcWork.Top;$taskMaxY=$taskMinY}
                $taskX=[Math]::Max($taskMinX,[Math]::Min($taskRect.Left,$taskMaxX))
                $taskY=[Math]::Max($taskMinY,[Math]::Min($taskRect.Top,$taskMaxY))
                if ($taskX -ne $taskRect.Left -or $taskY -ne $taskRect.Top -or
                    $taskWidth -ne $taskOriginalWidth -or $taskHeight -ne $taskOriginalHeight) {
                    if (![ReaderEmulatorWindow]::MoveWindow($taskHandle,$taskX,$taskY,$taskWidth,$taskHeight,$true)) {
                        throw '模拟器窗口移动失败。'
                    }
                    Write-Host "模拟器窗口已适配当前显示器工作区：($taskX, $taskY)，$taskWidth x $taskHeight。"
                } else {Write-Host '模拟器窗口已完整位于当前显示器工作区。'}
                return
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    throw '模拟器已启动，但未找到可定位的窗口。'
}
$taskDevices=Get-ReaderDevices
if ($taskDevices -match 'emulator-5554\s') {
    Assert-ReaderAvd
    $taskHeadless=@(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskSdk+'\',[StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -match '-avd\s+ReaderAosp35\b' -and $_.CommandLine -match '-port\s+5554\b' -and
        $_.CommandLine -match '(?:^|\s)-no-window(?:\s|$)'
    })
    if ($taskHeadless.Count) {
        Write-Host '将专用模拟器从无窗口测试模式重启为可见窗口；保留应用和相册数据。'
        & $taskAdb -s emulator-5554 emu kill
        if ($LASTEXITCODE -ne 0) {throw '无法关闭无窗口模拟器。'}
        for ($i=0;$i -lt 45;$i++) {
            $taskAlive=@($taskHeadless | Where-Object {Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue})
            if (!((Get-ReaderDevices) -match 'emulator-5554\s') -and !$taskAlive.Count) {break}
            Start-Sleep -Seconds 2
        }
        if (((Get-ReaderDevices) -match 'emulator-5554\s') -or $taskAlive.Count) {throw '旧模拟器尚未退出，请稍后重试。'}
        $taskDevices=Get-ReaderDevices
    }
}
if ($taskDevices -notmatch 'emulator-5554\s') {
    Write-Host '正在打开 ReaderAosp35 可见窗口…'
    Write-Host "模拟器原始诊断将写入：$taskEmulatorErr"
    $taskEmulatorProcess=Start-Process -FilePath $taskEmulator -ArgumentList @('-avd','ReaderAosp35','-no-snapshot','-no-boot-anim','-no-metrics','-crash-report-mode','never','-gpu','host','-port','5554') -WindowStyle Hidden -PassThru -RedirectStandardOutput $taskEmulatorOut -RedirectStandardError $taskEmulatorErr
} else {Write-Host '复用已经打开的 ReaderAosp35。'}
$taskBooted=$false
$taskAdbSeen=$false
if ($taskDevices -notmatch 'emulator-5554\s+device') {Write-Host '等待 ADB 连接…'}
for ($i=0;$i -lt 120;$i++) {
    Resume-ReaderConsole
    if ($taskEmulatorProcess -and $taskEmulatorProcess.HasExited) {
        $taskTail=@(Get-Content -LiteralPath $taskEmulatorErr -Tail 8 -ErrorAction SilentlyContinue)
        throw "模拟器进程提前退出（代码 $($taskEmulatorProcess.ExitCode)）。`n$($taskTail -join "`n")`n完整日志：$taskEmulatorErr"
    }
    if ((Get-ReaderDevices) -match 'emulator-5554\s+device') {
        if (!$taskAdbSeen) {Write-Host 'ADB 已连接，等待 Android 启动完成…';$taskAdbSeen=$true}
        $taskBoot=(& $taskAdb -s emulator-5554 shell getprop sys.boot_completed | Out-String).Trim()
        if ($taskBoot -eq '1') {$taskBooted=$true;break}
    }
    if ($i -gt 0 -and $i % 5 -eq 0) {Write-Host "仍在等待模拟器启动… $($i*2) 秒"}
    Start-Sleep -Seconds 2
}
if (!$taskBooted) {throw '模拟器启动超时，请查看 emulator.exe 控制台。'}
Assert-ReaderAvd
Set-ReaderEmulatorWindowVisible
$taskApi=(& $taskAdb -s emulator-5554 shell getprop ro.build.version.sdk | Out-String).Trim()
Write-Host "设备已就绪：ReaderAosp35 · Android API $taskApi · emulator-5554"
Write-Host '正在安装/更新 reader.apk（保留原设置）…'
$taskInstall=(& $taskAdb -s emulator-5554 install -r $taskApk | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {throw 'APK 安装失败。'}
Write-Host 'APK 已安装/更新。'
$taskLaunch=(& $taskAdb -s emulator-5554 shell am start -W -n org.local.reader/.MainActivity | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {throw '漫画朗读未能打开。'}
Write-Host '漫画朗读已打开。'
Write-Host '[完成] 模拟器和 APK 已就绪。若电脑服务未运行，请执行“仅启动电脑服务.bat”。'
Write-Host '在安卓点“保存并启动朗读浮窗”，允许整个屏幕投屏，再打开漫画点“▶ 重新开始”。'
if ($env:READER_NO_PAUSE -ne '1') {
    $taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskRoot 'config/runtime.json') | ConvertFrom-Json
    $taskPython=[string]$taskRuntime.python
    if (!(Test-Path -LiteralPath $taskPython)) {throw "缺少事件监视 Python：$taskPython"}
    Write-Host '接下来持续显示安卓事件；空闲时不刷新，按 Ctrl+C 只关闭监视。'
    & $taskPython -B (Join-Path $taskRoot 'tools/live_debug.py') android
}
