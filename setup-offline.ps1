$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath 'unified2api-app.tar')) {
    docker load -i unified2api-app.tar
}
foreach ($volume in @('unified2api_trae-auth','unified2api_trae-data','unified2api_buddy-data')) {
    docker volume create $volume | Out-Null
    $archive = "volumes/$volume.tar"
    docker run --rm -v "${volume}:/to" -v "${PSScriptRoot}:/from:ro" unified2api-app sh -c "tar -xf /from/$archive -C /to"
    if ($LASTEXITCODE -ne 0) { throw "Could not restore $volume" }
}
docker compose up -d --no-build --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed' }
Write-Host 'Unified Console: http://localhost:8080/admin/'
Write-Host 'Unified API: http://localhost:8080/v1'
