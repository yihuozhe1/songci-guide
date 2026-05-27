$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
  python -m venv .venv
}

& $py -m pip install --upgrade pip | Out-Host

if (Test-Path (Join-Path $root "requirements.txt")) {
  & $py -m pip install -r requirements.txt | Out-Host
}

& $py -m streamlit run app.py
