# Search Typeahead — one-command run (Windows / PowerShell)
# Generates the dataset on first run, then starts the server.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "data/queries.txt")) {
    Write-Host "[run] generating dataset (first run) ..." -ForegroundColor Cyan
    python scripts/generate_dataset.py
}

Write-Host "[run] starting server on http://127.0.0.1:8000" -ForegroundColor Green
python -m backend.server
