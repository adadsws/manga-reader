$ErrorActionPreference = 'Stop'

# Compatibility entry point; Python unittest creates a local ZIP without network use.
$projectRoot = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or !$projectRoot) {
    throw 'Cannot resolve the Git project root.'
}
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $projectRoot 'config/runtime.json') | ConvertFrom-Json
& $runtime.python -B -m unittest discover -s tests -p test_download_blue_archive_hina.py
if ($LASTEXITCODE -ne 0) {
    throw "Hina single-model download tests failed with exit code $LASTEXITCODE"
}
