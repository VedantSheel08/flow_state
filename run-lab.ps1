# FlowState Intelligence Lab launcher (Windows PowerShell): API on 127.0.0.1:8015, UI on http://localhost:5176
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install -q -r backend\requirements.txt numpy scikit-learn psutil
if (-not (Test-Path "lab\node_modules")) { Push-Location lab; npm install; Pop-Location }
$env:PYTHONIOENCODING = "utf-8"
if (-not (Test-Path "artifacts\lab\forecaster.pkl")) {
  Write-Host "No trained forecaster: generating the dataset and training (about 10 minutes)"
  Push-Location backend; & $py -m flowstate.lab.cli dataset; & $py -m flowstate.lab.cli train; Pop-Location
}
if (-not (Test-Path "data\lab\replays\index.json")) {
  Write-Host "Recording the default paired replays (about 2 minutes)"
  Push-Location backend; & $py -m flowstate.lab.cli record; Pop-Location
}
$backend = Start-Process -FilePath $py -ArgumentList "-m","flowstate.lab.cli","serve","--port","8015" -WorkingDirectory (Join-Path $root "backend") -PassThru -NoNewWindow
Write-Host "Lab API -> http://127.0.0.1:8015  (pid $($backend.Id))"
Write-Host "Lab UI  -> http://localhost:5176"
try {
  Push-Location lab
  npm run dev
} finally {
  Pop-Location
  if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
