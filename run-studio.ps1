# FlowState Studio launcher (Windows PowerShell): API on 127.0.0.1:8017, UI on http://localhost:5178
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install -q -r backend\requirements.txt numpy scikit-learn psutil opencv-python-headless gymnasium stable-baselines3
& $py -m pip install -q torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
if (-not (Test-Path "studio\node_modules")) { Push-Location studio; npm install; Pop-Location }
$env:PYTHONIOENCODING = "utf-8"
if (-not (Test-Path "artifacts\lab\forecaster.pkl")) {
  Write-Host "No trained forecaster: generating the dataset and training (about 10 minutes)"
  Push-Location backend; & $py -m flowstate.lab.cli dataset; & $py -m flowstate.lab.cli train; Pop-Location
}
if (-not (Test-Path "data\lab\replays\index.json")) {
  Write-Host "Recording the default paired replays (a few minutes)"
  Push-Location backend; & $py -m flowstate.lab.cli record; Pop-Location
}
$backend = Start-Process -FilePath $py -ArgumentList "-m","flowstate.lab.cli","serve","--port","8017" -WorkingDirectory (Join-Path $root "backend") -PassThru -NoNewWindow
Write-Host "Studio API -> http://127.0.0.1:8017  (pid $($backend.Id))"
Write-Host "Studio UI  -> http://localhost:5178"
try {
  Push-Location studio
  npm run dev
} finally {
  Pop-Location
  if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
