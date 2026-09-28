# 自动测试维护入口；兼容 Windows PowerShell 5，不依赖当前工作目录。
param(
    [Parameter(Mandatory=$true)][string]$Action,
    [string]$Source='',
    [ValidateSet('keep','on','off')][string]$Optimizations='keep'
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython=$taskRuntime.python
$taskAdb=Join-Path $taskRoot '~temp/android-sdk/platform-tools/adb.exe'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$taskRoot/~temp/ocrdeps;$taskRoot"
$env:HF_HUB_OFFLINE='1'
function Invoke-ReaderStep([string]$step) {
    $taskArguments=@('-B',(Join-Path $taskRoot 'tools/reproduce_test.py'),$step)
    if ($Source -and $step -in @('check','new')) {$taskArguments+=@('--source',$Source)}
    if ($step -eq 'setup') {$taskArguments+=@('--optimizations',$Optimizations)}
    & $taskPython @taskArguments
    if ($LASTEXITCODE -ne 0) {throw "步骤未完成：$step。请查看上方原因；已有文件会保留。"}
}
function Restart-Reader {
    $taskListener=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($taskListener) {
        $taskProcess=Get-Process -Id $taskListener.OwningProcess
        $taskRecords=Get-Content -Raw -Encoding UTF8 -LiteralPath '~temp/logs/processes.json' | ConvertFrom-Json
        $taskRecord=$taskRecords | Where-Object id -eq $taskProcess.Id | Select-Object -Last 1
        if (!$taskRecord) {throw '8765端口不是本启动器已记录的进程，请先检查占用。'}
        $taskRecordTime=if ($taskRecord.started -is [datetime]) {$taskRecord.started} else {[datetime]::Parse([string]$taskRecord.started,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)}
        if ([IO.Path]::GetFullPath($taskProcess.Path) -ne [IO.Path]::GetFullPath($taskRecord.path) -or $taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskRecordTime.ToUniversalTime().Ticks) {throw '进程身份不匹配，未停止该进程。'}
        Stop-Process -Id $taskProcess.Id
        $null=$taskProcess.WaitForExit(10000)
    }
    for ($i=0;$i -lt 30;$i++) {
        if (!(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {break}
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {throw '8765端口尚未释放。'}
    Start-Sleep -Seconds 3
    try { & (Join-Path $taskRoot 'tools/start.ps1') }
    catch {
        Write-Host '首次启动未就绪，等待端口/模型加载后重试一次。'
        Start-Sleep -Seconds 8
        & (Join-Path $taskRoot 'tools/start.ps1')
    }
}
function Start-ReaderEmulator {
    $taskDevices=(& $taskAdb devices | Out-String)
    if ($taskDevices -notmatch 'emulator-5554\s+device') {
        if ($taskDevices -notmatch 'emulator-5554') {
            $env:ANDROID_HOME=Join-Path $taskRoot '~temp/android-sdk'
            $env:ANDROID_AVD_HOME=Join-Path $taskRoot '~temp/avd'
            # 默认无窗口运行，系统控件由03通过无障碍结构操作；不显示漫画。
            $null=Start-Process -FilePath "$env:ANDROID_HOME/emulator/emulator.exe" -ArgumentList @('-avd','ReaderAosp35','-no-snapshot','-no-boot-anim','-no-metrics','-gpu','host','-port','5554','-no-window','-no-audio') -WindowStyle Hidden -PassThru -RedirectStandardOutput "$taskRoot/~temp/logs/reproduction-emulator.out.log" -RedirectStandardError "$taskRoot/~temp/logs/reproduction-emulator.err.log"
        }
        $taskBooted=$false
        for ($i=0;$i -lt 90;$i++) {
            try {$taskBoot=(& $taskAdb -s emulator-5554 shell getprop sys.boot_completed 2>$null | Out-String).Trim()} catch {$taskBoot=''}
            if ($taskBoot -eq '1') {$taskBooted=$true;break}
            Start-Sleep -Seconds 2
        }
        if (!$taskBooted) {throw '模拟器尚未完成启动；稍后重试02，或检查模拟器启动日志。'}
    }
    $taskDevices=(& $taskAdb devices | Out-String)
    if ($taskDevices -notmatch 'emulator-5554\s+device') {throw 'ReaderAosp35 在启动期间断开，请查看模拟器启动日志。'}
    $taskName=((& $taskAdb -s emulator-5554 emu avd name | Select-Object -First 1 | Out-String).Trim())
    if (!$taskName) {$taskName=((& $taskAdb -s emulator-5554 shell getprop ro.boot.qemu.avd_name | Out-String).Trim())}
    if ($LASTEXITCODE -ne 0 -or $taskName -ne 'ReaderAosp35') {throw '5554端口不是可核验的ReaderAosp35，拒绝继续。'}
    & $taskAdb -s emulator-5554 install -r "$taskRoot/reader.apk"
    if ($LASTEXITCODE -ne 0) {throw '正式APK安装失败。'}
    # 正式 APK 重装可能让系统清空已授权服务；仅在已核验的专用 AVD 中恢复本项目固定服务。
    & $taskAdb -s emulator-5554 shell settings put secure enabled_accessibility_services org.local.reader/.TurnService
    & $taskAdb -s emulator-5554 shell settings put secure accessibility_enabled 1
}
try {
    switch ($Action) {
        'new' {Invoke-ReaderStep 'new'; Restart-Reader; Start-ReaderEmulator}
        'restore' {Invoke-ReaderStep 'restore-files'; Restart-Reader; Invoke-ReaderStep 'restore-android'}
        default {Invoke-ReaderStep $Action}
    }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '如已运行01，请在排查后运行 tools/test_session.ps1 -Action restore 恢复原配置。'
    exit 1
}
