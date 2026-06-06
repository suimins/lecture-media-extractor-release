param(
  [Parameter(Mandatory = $true)]
  [string]$Urls,

  [string]$Out = "outputs",
  [string]$Model = "medium",

  [ValidateSet("cpu", "cuda")]
  [string]$Device = "cpu",

  [string]$ComputeType = "",
  [int]$BeamSize = 5,
  [string]$Language = "ko",
  [string]$Ffmpeg = "ffmpeg",
  [string]$Ffprobe = "ffprobe"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$appDir = Join-Path $root "m3u8"
$urlsPath = (Resolve-Path -LiteralPath $Urls).Path

if (-not [System.IO.Path]::IsPathRooted($Out)) {
  $Out = Join-Path $root $Out
}

if (-not $ComputeType) {
  if ($Device -eq "cuda") {
    $ComputeType = "int8_float16"
  } else {
    $ComputeType = "int8"
  }
}

$portableFfmpeg = Join-Path $root "tools\ffmpeg\bin\ffmpeg.exe"
$portableFfprobe = Join-Path $root "tools\ffmpeg\bin\ffprobe.exe"
if ($Ffmpeg -eq "ffmpeg" -and (Test-Path -LiteralPath $portableFfmpeg)) {
  $Ffmpeg = $portableFfmpeg
}
if ($Ffprobe -eq "ffprobe" -and (Test-Path -LiteralPath $portableFfprobe)) {
  $Ffprobe = $portableFfprobe
}

Push-Location $appDir
try {
  python -m app.main `
    --run-batch $urlsPath `
    --out $Out `
    --whisper-model $Model `
    --whisper-device $Device `
    --whisper-compute-type $ComputeType `
    --whisper-beam-size $BeamSize `
    --language $Language `
    --ffmpeg $Ffmpeg `
    --ffprobe $Ffprobe
} finally {
  Pop-Location
}
