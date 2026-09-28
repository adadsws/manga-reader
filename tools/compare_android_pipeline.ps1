# Repeat the official ReaderAosp35 MediaProjection flow for comparable modes.
param(
    [ValidateSet('off','on')][string[]]$Modes=@('off','on'),
    [ValidateRange(1,10)][int]$Repeats=3,
    [string]$Source=''
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (!$Source) {$Source=Join-Path $taskRoot '~temp/low-latency-pages-20260922'}
$taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython=$taskRuntime.python
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$taskRoot/~temp/ocrdeps;$taskRoot"
$env:HF_HUB_OFFLINE='1'
$taskResults=@()

function Invoke-Checked([string]$label,[scriptblock]$operation) {
    Write-Host "[$label]" -ForegroundColor Cyan
    & $operation
    if ($LASTEXITCODE -ne 0) {throw "$label 失败；请按当前批次证据恢复环境。"}
}

foreach ($taskMode in $Modes) {
    for ($taskRound=1;$taskRound -le $Repeats;$taskRound++) {
        Write-Host "Android 对照：$taskMode 第 $taskRound/$Repeats 轮" -ForegroundColor Green
        Invoke-Checked 'new' {& .\tools\test_session.ps1 -Action new -Source $Source}
        $taskActive=Get-Content -Raw -Encoding UTF8 -LiteralPath '~temp/reproduction/active.json' | ConvertFrom-Json
        Invoke-Checked 'prepare' {& $taskPython -B .\tools\reproduce_test.py prepare}
        Invoke-Checked 'setup' {& $taskPython -B .\tools\reproduce_test.py setup --optimizations $taskMode}
        Invoke-Checked 'collect' {& $taskPython -B .\tools\reproduce_test.py collect}
        Invoke-Checked 'report' {& $taskPython -B .\tools\reproduce_test.py report}
        Invoke-Checked 'restore' {& .\tools\test_session.ps1 -Action restore}
        $taskResults += [pscustomobject]@{mode=$taskMode;round=$taskRound;batch=$taskActive.id}
    }
}
$taskResults | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath '~temp/reproduction/low-latency-runs.json'
$taskResults | Format-Table -AutoSize
