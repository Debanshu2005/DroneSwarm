<#
.SYNOPSIS
    Syncs the DroneOS coordination module into DroneOS1/2/3 with namespace substitution.

.PARAMETER DryRun
    Report planned operations without writing any files.

.PARAMETER Source
    Source instance directory name (default: DroneOS).

.PARAMETER Targets
    Comma-separated list of target instance names (default: DroneOS1,DroneOS2,DroneOS3).

.EXAMPLE
    .\sync_coordination.ps1
    .\sync_coordination.ps1 -DryRun
    .\sync_coordination.ps1 -Source DroneOS -Targets DroneOS1,DroneOS2
#>
param(
    [switch]$DryRun,
    [string]$Source = "DroneOS",
    [string]$Targets = "DroneOS1,DroneOS2,DroneOS3"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent

$TargetList = $Targets -split ","

$TotalWritten = 0
$TotalSkipped = 0

foreach ($Target in $TargetList) {
    $Target = $Target.Trim()
    # Derive instance number: strip non-digit prefix
    $N = $Target -replace "[^0-9]", ""
    if (-not $N) {
        Write-Error "Cannot derive instance number from target '$Target'. Expected names like DroneOS1, DroneOS2, DroneOS3."
        exit 1
    }

    $SrcCoord = Join-Path $Root "$Source\core\coordination"
    $DstCoord = Join-Path $Root "$Target\core\coordination"
    $TargetRoot = Join-Path $Root $Target
    $SrcMain = Join-Path $Root "$Source\main.py"
    $DstMain = Join-Path $Root "$Target\main.py"
    $SrcTestCfg = Join-Path $Root "$Source\configs\flight.test.yaml"
    $DstCfgDir = Join-Path $Root "$Target\configs"
    $DstTestCfg = Join-Path $DstCfgDir "flight.test.yaml"

    Write-Host "`n=== Syncing $Source -> $Target (N=$N) ===" -ForegroundColor Cyan

    # Guard: source coordination dir must exist
    if (-not (Test-Path $SrcCoord)) {
        Write-Error "Source coordination directory not found: $SrcCoord"
        exit 1
    }

    # Guard: create target root if needed
    if (-not (Test-Path $TargetRoot)) {
        if ($DryRun) {
            Write-Host "[DRY RUN] MKDIR: $TargetRoot"
        } else {
            New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
        }
    }

    # ── Copy coordination subtree ──────────────────────────────────────────
    $SrcFiles = Get-ChildItem -Path $SrcCoord -Recurse -Filter "*.py"
    foreach ($File in $SrcFiles) {
        $RelPath = $File.FullName.Substring($SrcCoord.Length).TrimStart("\")
        $Dest = Join-Path $DstCoord $RelPath
        $DestDir = Split-Path -Parent $Dest

        # Read source, perform namespace substitution
        $Content = Get-Content -Path $File.FullName -Raw
        $Substituted = $Content `
            -replace "from DroneOS\.", "from DroneOS$N." `
            -replace "import DroneOS\.", "import DroneOS$N."

        if ($DryRun) {
            Write-Host "[DRY RUN] COPY: $($File.FullName) -> $Dest"
            continue
        }

        # Create destination directory
        if (-not (Test-Path $DestDir)) {
            New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
        }

        # Write only if content differs (idempotency)
        $Existing = $null
        if (Test-Path $Dest) {
            $Existing = Get-Content -Path $Dest -Raw
        }
        if ($Existing -eq $Substituted) {
            Write-Host "  SKIP (up-to-date): $RelPath"
            $TotalSkipped++
        } else {
            Set-Content -Path $Dest -Value $Substituted -Encoding UTF8 -NoNewline
            Write-Host "  WRITE: $RelPath"
            $TotalWritten++
        }
    }

    # ── Coordination wiring in main.py ────────────────────────────────────
    if (-not (Test-Path $DstMain)) {
        Write-Warning "  Target main.py not found: $DstMain — skipping wiring"
    } else {
        $DstMainContent = Get-Content -Path $DstMain -Raw
        if ($DstMainContent -match "create_formation_update_sender") {
            Write-Host "  SKIP (wiring already present): $Target\main.py"
            $TotalSkipped++
        } else {
            if ($DryRun) {
                Write-Host "[DRY RUN] WIRE: $DstMain (coordination block)"
            } else {
                # Extract the coordination wiring block from source main.py
                $SrcMainContent = Get-Content -Path $SrcMain -Raw
                # Match from the def create_formation_update_sender line
                # through the end of the coordination_manager dispatch block
                $Pattern = '(?s)(def create_formation_update_sender.*?coordination_manager\.run\(\)\))'
                if ($SrcMainContent -match $Pattern) {
                    $Block = $Matches[1]
                    # Namespace substitute
                    $Block = $Block `
                        -replace "from DroneOS\.", "from DroneOS$N." `
                        -replace "import DroneOS\.", "import DroneOS$N."
                    # Append to target main.py
                    Add-Content -Path $DstMain -Value "`n`n$Block" -Encoding UTF8
                    Write-Host "  WRITE (wiring): $Target\main.py"
                    $TotalWritten++
                } else {
                    Write-Warning "  Could not extract coordination block from $SrcMain — skipping wiring"
                }
            }
        }
    }

    # ── Test config ───────────────────────────────────────────────────────
    if (-not (Test-Path $SrcTestCfg)) {
        Write-Warning "  Source test config not found: $SrcTestCfg — skipping"
    } else {
        if ($DryRun) {
            Write-Host "[DRY RUN] COPY: $SrcTestCfg -> $DstTestCfg"
        } else {
            if (-not (Test-Path $DstCfgDir)) {
                New-Item -ItemType Directory -Force -Path $DstCfgDir | Out-Null
            }
            $SrcCfgContent = Get-Content -Path $SrcTestCfg -Raw
            $ExistingCfg = $null
            if (Test-Path $DstTestCfg) {
                $ExistingCfg = Get-Content -Path $DstTestCfg -Raw
            }
            if ($ExistingCfg -eq $SrcCfgContent) {
                Write-Host "  SKIP (up-to-date): configs\flight.test.yaml"
                $TotalSkipped++
            } else {
                Copy-Item -Path $SrcTestCfg -Destination $DstTestCfg -Force
                Write-Host "  WRITE: configs\flight.test.yaml"
                $TotalWritten++
            }
        }
    }
}

Write-Host "`nSync complete. Written: $TotalWritten, Skipped: $TotalSkipped" -ForegroundColor Green
exit 0
