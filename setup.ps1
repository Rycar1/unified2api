$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path -LiteralPath '.env')) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    $apiKey = [Convert]::ToBase64String($bytes)
    $rng.GetBytes($bytes)
    $adminKey = [Convert]::ToBase64String($bytes)
    $rng.Dispose()
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env'), "UNIFIED_API_KEY=$apiKey`nADMIN_KEY=$adminKey`n")
}
docker compose build
if ($LASTEXITCODE -ne 0) { throw 'Docker build failed' }
$legacy = docker compose -f compose.legacy.yaml ps --services --status running
if ($legacy -contains 'gateway' -or $legacy -contains 'trae' -or $legacy -contains 'codebuddy') {
    docker compose -f compose.legacy.yaml down
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop legacy services' }
}
docker compose up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed' }
Write-Host 'API: http://localhost:8080/v1'
Write-Host 'Console: http://localhost:8080/admin/'
Write-Host 'Keys are saved in .env'
