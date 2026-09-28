$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPidFile=Join-Path $taskRoot '~temp/logs/processes.json'
if (!(Test-Path -LiteralPath $taskPidFile)) {Write-Host '没有启动器记录的进程';exit}
$taskLoaded=Get-Content -Raw -Encoding UTF8 $taskPidFile | ConvertFrom-Json
foreach ($record in $taskLoaded) {
 if (!$record.id -or !$record.started -or !$record.path) {continue}
 $process=Get-Process -Id $record.id -ErrorAction SilentlyContinue
 if (!$process) {continue}
 $samePath=[IO.Path]::GetFullPath($process.Path) -eq [IO.Path]::GetFullPath([string]$record.path)
 $recordTime = if ($record.started -is [datetime]) {$record.started} else {[datetime]::Parse([string]$record.started, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)}
 $sameTime=$process.StartTime.ToUniversalTime().Ticks -eq $recordTime.ToUniversalTime().Ticks
 if ($samePath -and $sameTime) {Stop-Process -Id $process.Id}
}
Set-Content -Encoding UTF8 -LiteralPath $taskPidFile -Value '[]'
Write-Host '本启动器创建的电脑服务已停止'
