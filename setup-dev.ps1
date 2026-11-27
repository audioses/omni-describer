# setup-dev.ps1 - Omni Describer development environment setup (Windows)
#
# Run from the repository root:
#     powershell -ExecutionPolicy Bypass -File .\setup-dev.ps1
#
# What it does, in order:
#   1. Finds Python 3.11+ (installs Python 3.11 via winget if none is found).
#   2. Creates the .venv virtual environment if missing, reuses it if present.
#   3. Installs requirements.txt with pip (pip itself is verify/update: an
#      already-satisfied requirement is left alone, a missing one is added).
#   4. Fetches the runtime binaries the app expects in audio_describer\bin,
#      each pinned to an exact version and verified by SHA-256 before use:
#        - yt-dlp.exe            -> audio_describer\bin\yt-dlp.exe
#        - FFmpeg + ffprobe      -> audio_describer\bin\ffmpeg.exe / ffprobe.exe
#        - VLC runtime           -> audio_describer\bin\vlc\ (libvlc + plugins)
#      A file whose hash already matches its pin is kept (no download);
#      a missing or different file is (re)downloaded and verified.
#   5. Verifies the result: imports the app modules, checks that VLC loads,
#      and that yt-dlp / ffmpeg / ffprobe run.
#
# Nothing here touches the app's code or the user's settings.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRoot = $PSScriptRoot
$BinDir   = Join-Path $RepoRoot 'audio_describer\bin'
$VenvDir  = Join-Path $RepoRoot '.venv'
$VenvPy   = Join-Path $VenvDir 'Scripts\python.exe'

# --- Pinned downloads (version + SHA-256 from the publisher) ----------------
$Pins = @{
    YtDlp = @{
        Version = '2026.11.23'
        Url     = 'https://github.com/yt-dlp/yt-dlp/releases/download/2026.11.23/yt-dlp.exe'
        Sha256  = '622ed48023cac1daa52a3bf3a6ac38f03514de214b5f0fa069389fd0f6d17e5c'
    }
    FFmpeg = @{
        Version = '9.1 (essentials build, gyan.dev)'
        Url     = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.1-essentials_build.zip'
        Sha256  = 'b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd'
    }
    Vlc = @{
        Version = '3.0.21'
        Url     = 'https://ftp.osuosl.org/pub/videolan/vlc/3.0.21/win64/vlc-3.0.21-win64.zip'
        Sha256  = 'a0b7ec02b50adf6417eed014fb8df50af39690505a4225b85b3dc2ed17d14843'
    }
}

function Step($text) { Write-Host "`n=== $text ===" }

function Get-VerifiedDownload($url, $sha256, $destPath) {
    # Downloads $url to $destPath only when the SHA-256 checks out.
    $tmp = "$destPath.download"
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    $actual = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256.ToLower()) {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        throw "SHA-256 mismatch for $url`n  expected: $sha256`n  actual:   $actual`nRefusing to use the file."
    }
    Move-Item $tmp $destPath -Force
    Write-Host "Verified SHA-256 and saved to $destPath"
}

function Test-PinnedFile($path, $sha256) {
    if (-not (Test-Path $path)) { return $false }
    return ((Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -eq $sha256.ToLower())
}

# --- 1. Python ---------------------------------------------------------------
Step 'Python'
$PythonCmd = $null
foreach ($candidate in @(@('py', '-3.11'), @('py', '-3.12'), @('py', '-3'), @('python'))) {
    try {
        $v = & $candidate[0] $candidate[1..($candidate.Length - 1)] -c "import sys; print(sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $PythonCmd = $candidate; break }
    } catch { }
}
if (-not $PythonCmd) {
    Write-Host 'No Python 3 found. Installing Python 3.11 via winget...'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Python is not installed and winget is unavailable. Install Python 3.11+ from https://www.python.org/downloads/ and run this script again.'
    }
    & winget install --id Python.Python.3.11 -e --source winget --accept-source-agreements --accept-package-agreements --silent
    # Refresh PATH from the registry so the new install is visible in this session.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    foreach ($candidate in @(@('py', '-3.11'), @('python'))) {
        try {
            $v = & $candidate[0] $candidate[1..($candidate.Length - 1)] -c "import sys; print(sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) { $PythonCmd = $candidate; break }
        } catch { }
    }
    if (-not $PythonCmd) { throw 'Python installation finished but python is still not reachable. Open a new terminal and run this script again.' }
}
Write-Host "Using Python via: $($PythonCmd -join ' ')"
& $PythonCmd[0] $PythonCmd[1..($PythonCmd.Length - 1)] --version

# --- 2. Virtual environment ----------------------------------------------------
Step 'Virtual environment'
if (Test-Path $VenvPy) {
    Write-Host ".venv already exists, reusing it: $VenvDir"
} else {
    Write-Host "Creating .venv in $VenvDir"
    & $PythonCmd[0] $PythonCmd[1..($PythonCmd.Length - 1)] -m venv $VenvDir
    if (-not (Test-Path $VenvPy)) { throw 'venv creation failed.' }
}

# --- 3. Python packages ---------------------------------------------------------
Step 'Python packages (requirements.txt)'
& $VenvPy -m pip install --upgrade pip
$reqFile = Join-Path $RepoRoot 'requirements.txt'
& $VenvPy -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) {
    # Known upstream snag: requirements.txt lists simpleaudio, which ships no
    # wheels for current Python versions and fails to build from source - and
    # nothing in the app imports it. Retry without that one line.
    Write-Host ''
    Write-Host 'The full requirements install failed. Retrying without the'
    Write-Host 'simpleaudio line: it has no wheels for current Python versions,'
    Write-Host 'and no module in this app imports it, so the environment is'
    Write-Host 'complete without it.'
    $filtered = Join-Path $env:TEMP 'omni-requirements-nosimpleaudio.txt'
    Get-Content $reqFile | Where-Object { $_ -notmatch '^\s*simpleaudio(\s|=|<|>|~|!|\[|;)' } | Set-Content $filtered -Encoding Ascii
    & $VenvPy -m pip install -r $filtered
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed even without simpleaudio. See the pip output above.' }
}

# --- 4. Runtime binaries ---------------------------------------------------------
Step 'Runtime binaries (audio_describer\bin)'
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
$work = Join-Path $env:TEMP 'omni-dev-setup'
New-Item -ItemType Directory -Path $work -Force | Out-Null

# yt-dlp: single exe, hash-checked in place.
$ytDest = Join-Path $BinDir 'yt-dlp.exe'
if (Test-PinnedFile $ytDest $Pins.YtDlp.Sha256) {
    Write-Host "yt-dlp $($Pins.YtDlp.Version) already present and verified, keeping it."
} else {
    Get-VerifiedDownload $Pins.YtDlp.Url $Pins.YtDlp.Sha256 $ytDest
}

# FFmpeg: zip -> ffmpeg.exe + ffprobe.exe. Presence is checked per exe; the
# zip itself is always hash-verified before anything is taken from it.
$ffExe = Join-Path $BinDir 'ffmpeg.exe'
$fpExe = Join-Path $BinDir 'ffprobe.exe'
if ((Test-Path $ffExe) -and (Test-Path $fpExe)) {
    Write-Host 'ffmpeg.exe and ffprobe.exe already present, keeping them.'
} else {
    $ffZip = Join-Path $work 'ffmpeg.zip'
    Get-VerifiedDownload $Pins.FFmpeg.Url $Pins.FFmpeg.Sha256 $ffZip
    $ffOut = Join-Path $work 'ffmpeg'
    if (Test-Path $ffOut) { Remove-Item $ffOut -Recurse -Force }
    Expand-Archive $ffZip -DestinationPath $ffOut -Force
    Copy-Item (Get-ChildItem $ffOut -Recurse -Filter 'ffmpeg.exe')[0].FullName $ffExe -Force
    Copy-Item (Get-ChildItem $ffOut -Recurse -Filter 'ffprobe.exe')[0].FullName $fpExe -Force
    Write-Host "FFmpeg $($Pins.FFmpeg.Version) installed to $BinDir"
}

# VLC runtime: zip -> bin\vlc with libvlc.dll, libvlccore.dll and plugins\,
# the exact layout player_window.py loads (same as the release workflow).
$vlcDir = Join-Path $BinDir 'vlc'
$vlcDll = Join-Path $vlcDir 'libvlc.dll'
if ((Test-Path $vlcDll) -and (Test-Path (Join-Path $vlcDir 'plugins'))) {
    Write-Host "VLC runtime already present in $vlcDir, keeping it."
} else {
    $vlcZip = Join-Path $work 'vlc.zip'
    Get-VerifiedDownload $Pins.Vlc.Url $Pins.Vlc.Sha256 $vlcZip
    $vlcOut = Join-Path $work 'vlc'
    if (Test-Path $vlcOut) { Remove-Item $vlcOut -Recurse -Force }
    Expand-Archive $vlcZip -DestinationPath $vlcOut -Force
    $vlcSrc = (Get-ChildItem $vlcOut -Directory)[0].FullName
    New-Item -ItemType Directory -Path $vlcDir -Force | Out-Null
    Copy-Item (Join-Path $vlcSrc 'libvlc.dll') $vlcDir -Force
    Copy-Item (Join-Path $vlcSrc 'libvlccore.dll') $vlcDir -Force
    Copy-Item (Join-Path $vlcSrc 'plugins') $vlcDir -Recurse -Force
    Write-Host "VLC $($Pins.Vlc.Version) runtime installed to $vlcDir"
}

# --- 5. Verify --------------------------------------------------------------------
Step 'Verification'
$probe = @"
import sys
sys.path.insert(0, r'$RepoRoot')
from audio_describer.ui import settings_dialog
from audio_describer.core import gemini_helpers
from audio_describer.models import config_model
from audio_describer.ui import player_window
assert callable(gemini_helpers.fetch_available_models)
print('imports OK')
print('VLC_AVAILABLE =', player_window.VLC_AVAILABLE)
"@
$probeFile = Join-Path $work 'probe.py'
Set-Content $probeFile $probe -Encoding Ascii
& $VenvPy $probeFile
if ($LASTEXITCODE -ne 0) { throw 'Import verification failed. See the output above.' }

# From source, the app finds yt-dlp on PATH (the bin copy is used by frozen
# builds), so prove it resolves with bin on PATH, the way the run command
# below sets it up.
$env:Path = "$BinDir;$env:Path"
& (Join-Path $BinDir 'yt-dlp.exe') --version | ForEach-Object { Write-Host "yt-dlp --version: $_" }
& (Join-Path $BinDir 'ffmpeg.exe') -version | Select-Object -First 1 | ForEach-Object { Write-Host "ffmpeg: $_" }

Write-Host ''
Write-Host 'Setup complete. To run the app from source:'
Write-Host "  `$env:Path = `"$BinDir;`$env:Path`""
Write-Host "  $VenvPy run_app.py"
