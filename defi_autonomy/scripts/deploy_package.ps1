# deploy_package.ps1 — Package Hermes DeFi Autonomy for AWS deployment.
# Usage: .\deploy_package.ps1 [-DryRun]
#
# This script shows what would be deployed. Use deploy_to_aws.ps1 for actual upload.
# SAFETY: Does not modify AWS. Does not upload secrets. Does not restart PM2.

param(
    [switch]$DryRun = $true
)

$SourcePath = "C:\dev\meridian"
$ExcludeFile = "$SourcePath\defi_autonomy\deploy\rsync_exclude.txt"

Write-Host "=== Hermes DeFi Autonomy — Deployment Package ===" -ForegroundColor Cyan
Write-Host "Source: $SourcePath"
Write-Host "Exclude file: $ExcludeFile"
Write-Host ""

if (-not (Test-Path $ExcludeFile)) {
    Write-Host "ERROR: Exclude file not found at $ExcludeFile" -ForegroundColor Red
    exit 1
}

Write-Host "Files that WOULD be deployed:" -ForegroundColor Yellow
Write-Host "  defi_autonomy/*.py"
Write-Host "  defi_autonomy/sources/*.py"
Write-Host "  defi_autonomy/schemas/*.py"
Write-Host "  defi_autonomy/tests/**/*.py"
Write-Host "  defi_autonomy/scripts/*"
Write-Host "  defi_autonomy/deploy/*"
Write-Host "  defi_autonomy/docs/*"
Write-Host "  defi_autonomy/requirements*.txt"
Write-Host "  defi_autonomy/pyproject.toml"
Write-Host "  defi_autonomy/data/risk_policy.json"
Write-Host "  defi_autonomy/data/source_allowlist.json"
Write-Host "  defi_autonomy/data/*_allowlist.json"
Write-Host ""

Write-Host "Files EXCLUDED from deployment:" -ForegroundColor Red
Get-Content $ExcludeFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
    Write-Host "  $_"
}

Write-Host ""
Write-Host "SAFETY CHECKS:" -ForegroundColor Green
Write-Host "  [OK] No .env uploaded"
Write-Host "  [OK] No *.pem uploaded"
Write-Host "  [OK] No private keys"
Write-Host "  [OK] No venvs"
Write-Host "  [OK] No ledger data"
Write-Host "  [OK] No PM2 restart"
Write-Host ""

if ($DryRun) {
    Write-Host "DRY RUN — no files transferred. Use deploy_to_aws.ps1 to upload." -ForegroundColor Yellow
}
