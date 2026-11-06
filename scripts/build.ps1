[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path -Path "$PSScriptRoot" -ChildPath '..'))
$Python = Join-Path -Path "$ProjectRoot" -ChildPath '.venv\Scripts\python.exe'
$BuildRequirements = Join-Path -Path "$ProjectRoot" -ChildPath 'requirements-build.txt'
$RuntimeRequirements = Join-Path -Path "$ProjectRoot" -ChildPath 'requirements.txt'
$Icon = Join-Path -Path "$ProjectRoot" -ChildPath 'assets\app.ico'
$BuildSpec = Join-Path -Path "$ProjectRoot" -ChildPath 'scripts\CodexQuota.spec'
$DistDirectory = Join-Path -Path "$ProjectRoot" -ChildPath 'dist'
$WorkDirectory = Join-Path -Path "$ProjectRoot" -ChildPath 'build'

if (-not (Test-Path -LiteralPath "$Python" -PathType Leaf)) {
    & 'python' '-m' 'venv' (Join-Path -Path "$ProjectRoot" -ChildPath '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python virtual environment.' }
}

& "$Python" '-m' 'pip' 'install' '-r' "$RuntimeRequirements" '-r' "$BuildRequirements"
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the application/build dependencies.' }
if (-not (Test-Path -LiteralPath "$Icon" -PathType Leaf)) { throw "Application icon not found: $Icon" }
if (-not (Test-Path -LiteralPath "$BuildSpec" -PathType Leaf)) { throw "Build specification not found: $BuildSpec" }

Push-Location -LiteralPath "$ProjectRoot"
try {
    & "$Python" '-m' 'PyInstaller' '--noconfirm' '--clean' '--distpath' "$DistDirectory" '--workpath' "$WorkDirectory" "$BuildSpec"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller did not complete successfully.' }
}
finally {
    Pop-Location
}

Write-Host "Built: $(Join-Path -Path "$ProjectRoot" -ChildPath 'dist\CodexQuota\CodexQuota.exe')"
