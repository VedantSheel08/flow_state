# FlowState one-command launcher (Windows PowerShell)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install -q -r backend\requirements.txt
if (-not (Test-Path "data\networks\district.net.xml")) {
  Push-Location backend; & $py -m flowstate.cli build-network; Pop-Location
}
if (-not (Test-Path "frontend\node_modules")) { Push-Location frontend; npm install; Pop-Location }
$env:PYTHONIOENCODING = "utf-8"
$backend = Start-Process -FilePath $py -ArgumentList "-m","flowstate.cli","serve","--port","8000" -WorkingDirectory (Join-Path $root "backend") -PassThru -NoNewWindow
Write-Host "FlowState API  -> http://127.0.0.1:8000  (pid $($backend.Id))"
Write-Host "FlowState UI   -> http://127.0.0.1:5173"
try {
  Push-Location frontend
  npm run dev -- --host 127.0.0.1 --port 5173
} finally {
  Pop-Location
  if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
