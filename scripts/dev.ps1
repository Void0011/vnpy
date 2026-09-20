param(
    [ValidateSet('test', 'run', 'check', 'build')]
    [string]$Task = 'test'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Missing .venv. See docs/dca_development.md to create the project environment.'
}
Push-Location $projectRoot
try {
    switch ($Task) {
        'test' { & $projectPython -B -m pytest tests -q -p no:cacheprovider }
        'run' { & $projectPython run.py }
        'check' { & $projectPython -m ruff check . }
        'build' { & $projectPython -m build --wheel --no-isolation }
    }
    if ($LASTEXITCODE -ne 0) { throw "Project command failed: $LASTEXITCODE" }
}
finally { Pop-Location }
