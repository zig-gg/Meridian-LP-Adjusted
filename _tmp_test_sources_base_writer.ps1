$ErrorActionPreference = 'Stop'
$path = 'defi_autonomy\tests\unit\test_sources_base.py'
$content = @'
PLACEHOLDER
'@
Set-Content -Path $path -Value $content -NoNewline
'OK'
