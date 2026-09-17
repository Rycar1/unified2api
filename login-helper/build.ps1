$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$buildDir = Join-Path $env:TEMP 'unified-login-helper-build'
py -3.13 -m pip install 'playwright==1.58.0' 'pyinstaller==6.19.0'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
py -3.13 -m PyInstaller --noconfirm --onefile --windowed --name UnifiedLoginHelper --distpath "$PSScriptRoot/dist" --workpath "$buildDir/work" --specpath $buildDir "$PSScriptRoot/helper.py"
if ($LASTEXITCODE -ne 0) { throw 'Helper build failed' }
$downloadDir = Join-Path $projectRoot 'unified/downloads'
New-Item -ItemType Directory -Force $downloadDir | Out-Null
Compress-Archive -LiteralPath "$PSScriptRoot/dist/UnifiedLoginHelper.exe", "$PSScriptRoot/安装说明.txt" -DestinationPath "$downloadDir/monkey-login-helper.zip" -Force
Write-Host 'Helper package is ready. Rebuild the app image to include the download.'
