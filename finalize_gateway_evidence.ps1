$ErrorActionPreference = "Stop"

Write-Host "`n=== Store-and-Forward IoT Gateway: Final Evidence Run ===" -ForegroundColor Cyan

# Ensure commands are being run from the project root.
if (-not (Test-Path ".\gateway_azure_integrated.py")) {
    throw "gateway_azure_integrated.py was not found. Put this script in the project folder and run it from that folder."
}
if (-not (Test-Path ".\results\metrics.json")) {
    throw "results\metrics.json was not found. Run your local duplicate test first."
}
if (-not (Test-Path ".\results\message_log.csv")) {
    throw "results\message_log.csv was not found. Run your local duplicate test first."
}

# Preserve local duplicate-test evidence without overwriting an existing backup.
if (-not (Test-Path ".\results\metrics_local_duplicate_test.json")) {
    Copy-Item ".\results\metrics.json" ".\results\metrics_local_duplicate_test.json"
}
if (-not (Test-Path ".\results\message_log_local_duplicate_test.csv")) {
    Copy-Item ".\results\message_log.csv" ".\results\message_log_local_duplicate_test.csv"
}

Write-Host "`nLocal duplicate-test evidence saved." -ForegroundColor Green

# Use a new database filename to avoid deleting or modifying the earlier Azure test database.
Write-Host "`nStarting a fresh Azure IoT Hub test. This sends 100 telemetry messages." -ForegroundColor Yellow
python ".\gateway_azure_integrated.py" --azure --db "azure_gateway_final.db" --reset --readings 100 --outage-readings 60
if ($LASTEXITCODE -ne 0) {
    throw "Azure test failed. Your local duplicate-test backups remain saved. Review the error above before retrying."
}

# Preserve the result files produced by the Azure run.
Copy-Item ".\results\metrics.json" ".\results\metrics_azure_test.json" -Force
Copy-Item ".\results\message_log.csv" ".\results\message_log_azure_test.csv" -Force

Write-Host "`n=== Evidence files ===" -ForegroundColor Cyan
Get-ChildItem ".\results" |
    Where-Object { $_.Name -match '^(metrics|message_log)_(local_duplicate_test|azure_test)\.(json|csv)$' } |
    Select-Object Name, Length, LastWriteTime |
    Format-Table -AutoSize

Write-Host "`n=== Azure test summary ===" -ForegroundColor Cyan
Get-Content ".\results\metrics_azure_test.json"

Write-Host "`nFinished. Keep .env private; do not include it in your submission." -ForegroundColor Green
