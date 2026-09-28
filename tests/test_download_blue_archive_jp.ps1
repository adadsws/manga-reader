$ErrorActionPreference = 'Stop'

# 测试真实 BAT 的参数与分支行为，仅用本地假 git 隔离网络下载。
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot 'download_blue_archive_jp.bat'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('blue-archive-jp-bat-test-' + [guid]::NewGuid())
$fakeBin = Join-Path $testRoot 'bin'
$downloadRoot = Join-Path $testRoot 'downloads'
$logPath = Join-Path $testRoot 'git.log'

try {
    New-Item -ItemType Directory -Force -Path $fakeBin | Out-Null

    @'
@echo off
>>"%TEST_GIT_LOG%" echo %*
if "%1"=="clone" (
    mkdir "%~5\.git" >nul 2>nul
)
exit /b 0
'@ | Set-Content -LiteralPath (Join-Path $fakeBin 'git.cmd') -Encoding ascii

    $env:TEST_GIT_LOG = $logPath
    $env:PATH = "$fakeBin;$env:PATH"
    $env:CI = '1'

    & cmd.exe /d /c ('"{0}" "{1}"' -f $scriptPath, $downloadRoot)
    if ($LASTEXITCODE -ne 0) {
        throw "BAT first run exited with $LASTEXITCODE"
    }

    $firstRunLog = Get-Content -Raw -LiteralPath $logPath -Encoding utf8
    if ($firstRunLog -notmatch 'clone --depth 1') {
        throw "First run did not perform a shallow clone. Git log: $firstRunLog"
    }
    if ($firstRunLog -notmatch 'lfs pull --include="?蔚蓝档案/日语/\*\*"?') {
        throw "First run did not restrict Git LFS to Japanese Blue Archive models. Git log: $firstRunLog"
    }

    Clear-Content -LiteralPath $logPath
    & cmd.exe /d /c ('"{0}" "{1}"' -f $scriptPath, $downloadRoot)
    if ($LASTEXITCODE -ne 0) {
        throw "BAT resume run exited with $LASTEXITCODE"
    }

    $resumeLog = Get-Content -Raw -LiteralPath $logPath -Encoding utf8
    if ($resumeLog -match 'clone --depth 1') {
        throw 'Resume run cloned the repository again.'
    }
    if ($resumeLog -notmatch 'lfs pull --include="?蔚蓝档案/日语/\*\*"?') {
        throw 'Resume run did not continue the targeted Git LFS pull.'
    }

    Write-Output 'PASS: first run and resume run use only 蔚蓝档案/日语/**.'
}
finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
