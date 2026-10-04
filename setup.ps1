param([switch]$SkipGpuSetup)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    # A private Python avoids requiring py.exe, PATH changes or administrator access.
    $bootstrap = Join-Path $PSScriptRoot 'data\bootstrap'
    New-Item -ItemType Directory -Path $bootstrap -Force | Out-Null
    $uv = Join-Path $bootstrap 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        Write-Output 'Downloading the Python bootstrap tool...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $archivePath = Join-Path $bootstrap 'uv.zip'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri 'https://github.com/astral-sh/uv/releases/download/0.12.23/uv-x86_64-pc-windows-msvc.zip' -OutFile $archivePath
        if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne '75d05de6762778c31ee183398de7dd15093fad0ed90b1f236d8205ea5ec00c90') {
            throw 'Python bootstrap checksum mismatch. Rerun setup to download it again.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            $entry = $archive.GetEntry('uv.exe')
            if (-not $entry) { throw 'The Python bootstrap archive is incomplete.' }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $uv, $true)
        } finally { $archive.Dispose() }
        Remove-Item -LiteralPath $archivePath
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot 'data\python'
    Write-Output 'Preparing private Python 3.12 and pip...'
    & $uv --no-config venv --managed-python --python cpython-3.12-windows-x86_64-none --seed .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python setup failed. Check the connection and rerun setup.' }
}
& '.\.venv\Scripts\python.exe' -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'pip installation failed.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not $SkipGpuSetup) {
    & '.\.venv\Scripts\python.exe' -m lecture_script.runtime
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'GPU setup did not complete. CPU mode remains available. Rerun setup.cmd to retry GPU setup.'
    }
}
& '.\.venv\Scripts\python.exe' install_chrome.py
if ($LASTEXITCODE -ne 0) { throw 'Chrome native host registration failed.' }
Write-Output 'Ready. Open install-guide.html to load the Chrome extension.'
