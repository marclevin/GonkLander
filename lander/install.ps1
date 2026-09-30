# GonkLander bootstrap for Windows.
#
#   irm https://marclevin.me/gonk.ps1 | iex
#
# This script is public. It contains no credentials and asks for none.
# It needs no administrator rights, installs only into your user profile,
# and is safe to run again: a second run upgrades or repairs the first.
#
# Optional environment variables:
#   GONK_VERSION   tag or branch to install                  (default: main)
#   GONK_REPO      GitHub repository, as owner/name           (default: marclevin/GonkLander)
#   GONK_SOURCE    install from this path or URL instead
#   GONK_PROFILE   after installing, run `gonk land <profile>`
#
# Status: written to mirror install.sh, not yet run on a real Windows machine.
# Kept to plain ASCII on purpose: Windows PowerShell 5 misreads anything else
# in a script that has no byte-order mark.

$ErrorActionPreference = "Stop"

function Write-Ok($Message)   { Write-Host "  " -NoNewline; Write-Host "ok" -ForegroundColor Green -NoNewline; Write-Host " $Message" }
function Write-Warn($Message) { Write-Host "  " -NoNewline; Write-Host "!" -ForegroundColor Yellow -NoNewline; Write-Host " $Message" }

function Stop-WithHelp($Message, [string[]]$Hints) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    if ($Hints) {
        Write-Host ""
        Write-Host "Try:"
        foreach ($hint in $Hints) { Write-Host "    $hint" }
    }
    # `throw` rather than `exit`: under `irm | iex`, exit would close the window.
    throw "Gonk was not installed."
}

function Get-GonkSource($Repo, $Version) {
    if ($env:GONK_SOURCE) { return $env:GONK_SOURCE }

    # A local checkout wins, so that .\lander\install.ps1 installs the code next to it.
    if ($PSScriptRoot) {
        $root = Split-Path -Parent $PSScriptRoot
        $project = Join-Path $root "pyproject.toml"
        if ((Test-Path $project) -and (Select-String -Path $project -Pattern '^name = "gonklander"' -Quiet)) {
            return $root
        }
    }

    if ($Version -match '^v[0-9]') {
        return "https://github.com/$Repo/archive/refs/tags/$Version.zip"
    }
    return "https://github.com/$Repo/archive/refs/heads/$Version.zip"
}

function Install-Gonk {
    $repo = if ($env:GONK_REPO) { $env:GONK_REPO } else { "marclevin/GonkLander" }
    $version = if ($env:GONK_VERSION) { $env:GONK_VERSION } else { "main" }
    $binDir = Join-Path $env:USERPROFILE ".local\bin"
    $originalPath = $env:PATH

    Write-Host "GonkLander: making this machine Marc-compatible."
    Write-Host ""

    # --- platform
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -notin @("AMD64", "ARM64")) {
        Stop-WithHelp "Gonk does not support $arch processors yet." @("Supported: AMD64, ARM64.")
    }
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Stop-WithHelp "Gonk needs PowerShell 5 or newer." @("Update Windows, or install PowerShell 7.")
    }
    Write-Ok "Platform: windows $arch"

    # --- uv
    $env:PATH = "$binDir;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "  Installing uv (Python package manager)..."
        try {
            $env:UV_NO_MODIFY_PATH = "1"
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
        } catch {
            Stop-WithHelp "The uv installer did not finish." @(
                "Check this machine's Internet connection.",
                "Or install uv yourself: https://docs.astral.sh/uv/"
            )
        }
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-WithHelp "uv was installed, but cannot be found in $binDir." @()
    }
    Write-Ok "uv: $(uv --version)"

    # --- gonk
    $source = Get-GonkSource $repo $version
    Write-Host "  Installing gonk from $source..."
    $spec = if ($source -match '^https?://') { "gonklander @ $source" } else { $source }
    $output = & uv tool install --force --reinstall --quiet $spec 2>&1
    if ($LASTEXITCODE -ne 0) {
        $output | Select-Object -Last 15 | ForEach-Object { Write-Host $_ }
        Stop-WithHelp "gonk could not be installed from $source." @(
            "Check this machine's Internet connection.",
            "Check that the version exists: GONK_VERSION=$version"
        )
    }

    $gonk = Join-Path $binDir "gonk.exe"
    if (-not (Test-Path $gonk)) { $gonk = Join-Path (uv tool dir --bin) "gonk.exe" }
    if (-not (Test-Path $gonk)) {
        Stop-WithHelp "gonk was installed, but its command cannot be found." @()
    }
    Write-Ok "gonk: $(& $gonk --version)"

    # --- PATH
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if (($originalPath -split ";") -contains $binDir -or ($userPath -split ";") -contains $binDir) {
        Write-Ok "$binDir is on your PATH"
    } else {
        Write-Warn "$binDir is not on your PATH, so your shell will not find gonk yet."
        Write-Host ""
        Write-Host "    To add it for your user, run:"
        Write-Host ""
        Write-Host "        [Environment]::SetEnvironmentVariable('PATH', `"$binDir;`$([Environment]::GetEnvironmentVariable('PATH','User'))`", 'User')"
        Write-Host ""
        Write-Host "    Until then, run gonk as: $gonk"
    }

    Write-Host ""
    Write-Host "Gonk has landed."
    Write-Host ""
    if ($env:GONK_PROFILE) {
        & $gonk land $env:GONK_PROFILE
    } else {
        Write-Host "Next:"
        Write-Host "    gonk doctor      see what this machine has and lacks"
        Write-Host "    gonk land dev    install your usual tools"
    }
}

# Defined above, called here: a download that is cut short defines nothing
# complete and runs nothing.
Install-Gonk
