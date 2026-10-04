param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'LectureScript'),
    [switch]$NoOpen,
    [switch]$SkipGpuSetup
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Start-Transcript -Path (Join-Path $InstallDir 'install.log') -Append | Out-Null
Push-Location -LiteralPath $InstallDir
try {
    Write-Output "Installing Lecture Script in: $InstallDir"
    if ($PSScriptRoot.TrimEnd('\') -ne $InstallDir.TrimEnd('\')) {
        $files = @('setup.cmd', 'setup.ps1', 'start.cmd', 'install-chrome.cmd', 'install_chrome.py',
            'requirements.txt', 'requirements-gpu.txt', 'install-guide.html', 'windows-guide.html',
            'README.md', 'README-WINDOWS.txt', 'LICENSE')
        foreach ($file in $files) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $InstallDir $file) -Force
        }
        foreach ($folder in @('lecture_script', 'extension')) {
            $destination = Join-Path $InstallDir $folder
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot $folder) -File |
                Where-Object { $_.Extension -in @('.py', '.js', '.json', '.css', '.html') } |
                ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination $_.Name) -Force }
        }
    }
    & (Join-Path $InstallDir 'setup.ps1') -SkipGpuSetup:$SkipGpuSetup

    # Native Messaging is registered above. Unpacked extensions require a user
    # action in normal Chrome; do not modify Chrome profiles or enterprise policy.
    if (-not $NoOpen) {
        $chromeLocations = @(
            (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
            (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
        )
        $chrome = $chromeLocations | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $chrome) {
            Write-Output 'Downloading the official Google Chrome installer...'
            $chromeInstaller = Join-Path $InstallDir 'data\bootstrap\ChromeSetup.exe'
            New-Item -ItemType Directory -Path (Split-Path $chromeInstaller) -Force | Out-Null
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri 'https://dl.google.com/chrome/install/latest/chrome_installer.exe' -OutFile $chromeInstaller
            $signature = Get-AuthenticodeSignature -LiteralPath $chromeInstaller
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Google LLC(?:,|$)') {
                throw 'Google Chrome installer signature could not be verified.'
            }
            Write-Output 'Complete the Google Chrome installer if it asks for confirmation.'
            Start-Process -FilePath $chromeInstaller -Wait
            $chrome = $chromeLocations | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $chrome) { throw 'Chrome was not installed. Install Google Chrome, then rerun START-HERE.cmd.' }
        }
        $guide = ([Uri](Join-Path $InstallDir 'windows-guide.html')).AbsoluteUri
        $chromeArguments = '--new-window "' + $guide + '" "chrome://extensions/"'
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Lecture Script - Chrome setup.lnk'))
        $shortcut.TargetPath = $chrome
        $shortcut.Arguments = $chromeArguments
        $shortcut.Save()
        Start-Process -FilePath $chrome -ArgumentList $chromeArguments
    }
    Write-Output 'Setup complete. In Chrome: Developer mode > Load unpacked > select this folder:'
    Write-Output (Join-Path $InstallDir 'extension')
    Write-Output 'After adding the extension, choose MP4 / MP3 / Script and open your lecture.'
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
