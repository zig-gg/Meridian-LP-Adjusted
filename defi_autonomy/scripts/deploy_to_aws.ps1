# deploy_to_aws.ps1 — Deploy Hermes DeFi Autonomy to AWS EC2.
# Usage: .\deploy_to_aws.ps1 [-DryRun] [-KeyFile path\to\key.pem]
#
# SAFETY:
#   - Does NOT restart PM2 (operator must do this manually)
#   - Does NOT upload .env, private keys, or secrets
#   - Does NOT modify risk_policy.json on AWS
#   - Uses rsync exclude list to skip unsafe files
#
# After upload, SSH into AWS and manually:
#   cd /home/ubuntu/hermes-agent
#   source .venv-defi/bin/activate
#   pip install -r defi_autonomy/requirements-dry.txt
#   python -m pytest defi_autonomy/tests -q
#   pm2 restart Hermes-DeFi-Autonomy-Dry  # only after tests pass

param(
    [switch]$DryRun = $false,
    [string]$KeyFile = "",
    [string]$AwsHost = "ubuntu@13.238.253.243",
    [string]$RemotePath = "/home/ubuntu/hermes-agent"
)

$SourcePath = "C:\dev\meridian\"
$ExcludeFile = "defi_autonomy\deploy\rsync_exclude.txt"

Write-Host "=== Hermes DeFi Autonomy — AWS Deployment ===" -ForegroundColor Cyan
Write-Host "Source: $SourcePath"
Write-Host "Target: ${AwsHost}:${RemotePath}"
Write-Host ""

# Build rsync command
$rsyncArgs = @(
    "-avz",
    "--exclude-from=$SourcePath$ExcludeFile",
    "--delete-excluded"
)

if ($DryRun) {
    $rsyncArgs += "--dry-run"
    Write-Host "DRY RUN MODE — showing what would be transferred" -ForegroundColor Yellow
}

if ($KeyFile) {
    $rsyncArgs += "-e"
    $rsyncArgs += "ssh -i $KeyFile"
}

$rsyncArgs += $SourcePath
$rsyncArgs += "${AwsHost}:${RemotePath}"

Write-Host ""
Write-Host "Command:" -ForegroundColor Gray
Write-Host "  rsync $($rsyncArgs -join ' ')"
Write-Host ""

if (-not $DryRun) {
    Write-Host "Executing rsync..." -ForegroundColor Green
    & rsync @rsyncArgs
    Write-Host ""
    Write-Host "Upload complete." -ForegroundColor Green
    Write-Host ""
    Write-Host "NEXT STEPS (manual on AWS):" -ForegroundColor Yellow
    Write-Host "  ssh ${AwsHost}"
    Write-Host "  cd $RemotePath"
    Write-Host "  source .venv-defi/bin/activate"
    Write-Host "  pip install -r defi_autonomy/requirements-dry.txt"
    Write-Host "  python -m pytest defi_autonomy/tests -q"
    Write-Host "  # Only after tests pass:"
    Write-Host "  pm2 restart Hermes-DeFi-Autonomy-Dry"
} else {
    Write-Host "No files transferred (dry run)." -ForegroundColor Yellow
}
