[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path -Path "$PSScriptRoot" -ChildPath '..'))
$BuiltExe = Join-Path -Path "$ProjectRoot" -ChildPath 'dist\CodexQuota\CodexQuota.exe'
$Pythonw = Join-Path -Path "$ProjectRoot" -ChildPath '.venv\Scripts\pythonw.exe'
$Icon = Join-Path -Path "$ProjectRoot" -ChildPath 'assets\app.ico'
$Desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$ShortcutPath = Join-Path -Path "$Desktop" -ChildPath 'Codex Quota.lnk'

if (Test-Path -LiteralPath "$BuiltExe" -PathType Leaf) {
    $Target = "$BuiltExe"
    $Arguments = ''
}
elseif (Test-Path -LiteralPath "$Pythonw" -PathType Leaf) {
    $Target = "$Pythonw"
    $Arguments = '-m quota_widget'
}
else {
    throw 'Start the widget once to install dependencies, or run scripts\build.ps1 first.'
}

$Shell = New-Object -ComObject 'WScript.Shell'
$Shortcut = $Shell.CreateShortcut("$ShortcutPath")
$Shortcut.TargetPath = "$Target"
$Shortcut.Arguments = "$Arguments"
$Shortcut.WorkingDirectory = "$ProjectRoot"
$Shortcut.Description = 'ChatGPT / Codex quota desktop widget'
if (Test-Path -LiteralPath "$Icon" -PathType Leaf) { $Shortcut.IconLocation = "$Icon,0" }
$Shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath"
