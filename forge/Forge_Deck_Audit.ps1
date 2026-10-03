param(
    [string]$ForgeDir = "$env:USERPROFILE\Forge",
    [int]$GamesPerDeck = 1
)

$ErrorActionPreference = "Stop"

$Jar = Join-Path $ForgeDir "forge-gui-desktop-2.0.15-jar-with-dependencies.jar"
$ConstructedDir = Join-Path $env:APPDATA "Forge\decks\constructed"
$CommanderDir   = Join-Path $env:APPDATA "Forge\decks\commander"
$LogDir = Join-Path $ForgeDir "Forge_Deck_Audit"

$Decks = @(
    @{ Name = "Abzan_Food";            Format = "Constructed"; Dir = $ConstructedDir },
    @{ Name = "Rakdos_Orcs";           Format = "Constructed"; Dir = $ConstructedDir },
    @{ Name = "Frodo_Sam_Food_Ring";   Format = "Commander";   Dir = $CommanderDir },
    @{ Name = "Saruman_Spell_Amass";   Format = "Commander";   Dir = $CommanderDir }
)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Jar)) {
    throw "Forge JAR not found: $Jar"
}

function Get-DeckInfo {
    param([string]$Path)

    $lines = Get-Content -LiteralPath $Path
    $section = ""
    $mainCount = 0
    $commanderCount = 0
    $invalidLines = @()
    $cards = @()

    foreach ($line in $lines) {
        $trim = $line.Trim()

        if ($trim -match '^\[(.+)\]$') {
            $section = $Matches[1].ToLowerInvariant()
            continue
        }

        if ($trim -eq "" -or $trim.StartsWith("#")) {
            continue
        }

        if ($section -in @("main","commander")) {
            if ($trim -match '^\s*(\d+)\s+(.+?)\|([A-Za-z0-9]+)\|\[(.+)\]\s*$') {
                $qty = [int]$Matches[1]
                $card = $Matches[2].Trim()
                $set  = $Matches[3].Trim()
                $num  = $Matches[4].Trim()

                $cards += [pscustomobject]@{
                    Section = $section
                    Quantity = $qty
                    Card = $card
                    Set = $set
                    Collector = $num
                }

                if ($section -eq "main") { $mainCount += $qty }
                if ($section -eq "commander") { $commanderCount += $qty }
            }
            else {
                $invalidLines += $trim
            }
        }
    }

    [pscustomobject]@{
        MainCount = $mainCount
        CommanderCount = $commanderCount
        TotalCount = $mainCount + $commanderCount
        InvalidLines = $invalidLines
        Cards = $cards
    }
}

function Invoke-ForgeSimulation {
    param(
        [string]$DeckName,
        [string]$Format,
        [string]$LogPath
    )

    Push-Location $ForgeDir
    try {
        $args = @(
            "-jar", $Jar,
            "sim",
            "-d", $DeckName, $DeckName
        )

        if ($Format -eq "Commander") {
            $args += "-f"
            $args += "Commander"
        }

        $args += "-n"
        $args += "$GamesPerDeck"
        $args += "-q"

        # Capture both stdout and stderr because Forge writes important diagnostics to stderr.
        $output = & java @args 2>&1 | Out-String
        $output | Set-Content -LiteralPath $LogPath -Encoding UTF8
        return $output
    }
    finally {
        Pop-Location
    }
}

$Results = @()

Write-Host ""
Write-Host "============================================================"
Write-Host "                 FORGE DECK AUDIT"
Write-Host "============================================================"
Write-Host "Forge: $ForgeDir"
Write-Host "Games/deck self-test: $GamesPerDeck"
Write-Host "Logs: $LogDir"
Write-Host ""

foreach ($deck in $Decks) {
    $name = $deck.Name
    $format = $deck.Format
    $path = Join-Path $deck.Dir "$name.dck"
    $logPath = Join-Path $LogDir "${name}_${format}.log"

    Write-Host "[$name]"

    if (-not (Test-Path $path)) {
        Write-Host "  File            FAIL - not found: $path" -ForegroundColor Red
        $Results += [pscustomobject]@{
            Deck=$name; Format=$format; Main=0; Commander=0; Total=0
            Structure="FAIL"; ForgeLoad="NOT RUN"; Unsupported=0; AIWarnings=0
        }
        continue
    }

    $info = Get-DeckInfo -Path $path

    $expected = if ($format -eq "Commander") { 100 } else { 60 }
    $structurePass = ($info.TotalCount -eq $expected -and $info.InvalidLines.Count -eq 0)

    if ($structurePass) {
        Write-Host "  Structure       PASS" -ForegroundColor Green
    } else {
        Write-Host "  Structure       FAIL" -ForegroundColor Red
    }

    Write-Host "  Main cards      $($info.MainCount)"
    Write-Host "  Commander cards $($info.CommanderCount)"
    Write-Host "  Total           $($info.TotalCount)/$expected"

    if ($info.InvalidLines.Count -gt 0) {
        Write-Host "  Invalid lines:"
        $info.InvalidLines | ForEach-Object { Write-Host "    $_" }
    }

    Write-Host "  Running Forge self-test..."

    $output = Invoke-ForgeSimulation -DeckName $name -Format $format -LogPath $logPath

    $unsupportedMatches = [regex]::Matches(
        $output,
        'An unsupported card was requested:\s*"([^"]+)"\s+from\s+"([^"]+)"',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    $unsupported = @()
    foreach ($m in $unsupportedMatches) {
        $unsupported += "$($m.Groups[1].Value) [$($m.Groups[2].Value)]"
    }
    $unsupported = @($unsupported | Sort-Object -Unique)

    $warningLines = @(
        ($output -split "`r?`n") |
        Where-Object { $_ -match '^\s*Warning:' } |
        ForEach-Object { $_.Trim() } |
        Sort-Object -Unique
    )

    $unknownLines = @(
        ($output -split "`r?`n") |
        Where-Object {
            $_ -match 'unknown card' -or
            $_ -match 'could not find this card' -or
            $_ -match 'Could not load deck' -or
            $_ -match 'Exception' -or
            $_ -match 'ERROR'
        } |
        ForEach-Object { $_.Trim() } |
        Sort-Object -Unique
    )

    $gameStarted = ($output -match 'one game of')
    $loadFailed = ($output -match 'Could not load deck' -or $output -match 'Exception')
    $forgeLoad = if ($loadFailed) { "FAIL" } elseif ($gameStarted) { "PASS" } else { "UNCERTAIN" }

    if ($forgeLoad -eq "PASS") {
        Write-Host "  Forge Load       PASS" -ForegroundColor Green
    } elseif ($forgeLoad -eq "FAIL") {
        Write-Host "  Forge Load       FAIL" -ForegroundColor Red
    } else {
        Write-Host "  Forge Load       UNCERTAIN" -ForegroundColor Yellow
    }

    Write-Host "  Unsupported      $($unsupported.Count)"
    foreach ($u in $unsupported) {
        Write-Host "    - $u" -ForegroundColor Red
    }

    Write-Host "  AI warnings      $($warningLines.Count)"
    foreach ($w in $warningLines) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }

    if ($unknownLines.Count -gt 0) {
        Write-Host "  Other diagnostics:"
        foreach ($x in $unknownLines) {
            Write-Host "    - $x"
        }
    }

    Write-Host "  Raw log          $logPath"
    Write-Host ""

    $Results += [pscustomobject]@{
        Deck=$name
        Format=$format
        Main=$info.MainCount
        Commander=$info.CommanderCount
        Total=$info.TotalCount
        Structure=if ($structurePass) {"PASS"} else {"FAIL"}
        ForgeLoad=$forgeLoad
        Unsupported=$unsupported.Count
        AIWarnings=$warningLines.Count
    }
}

$csvPath = Join-Path $LogDir "Forge_Deck_Audit_Summary.csv"
$txtPath = Join-Path $LogDir "Forge_Deck_Audit_Summary.txt"

$Results | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8

$report = @()
$report += "=== FORGE DECK AUDIT ==="
$report += "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$report += "Forge: $ForgeDir"
$report += "Games/deck self-test: $GamesPerDeck"
$report += ""

foreach ($r in $Results) {
    $report += $r.Deck
    $report += "  Format          $($r.Format)"
    $report += "  Main            $($r.Main)"
    $report += "  Commander       $($r.Commander)"
    $report += "  Total           $($r.Total)"
    $report += "  Structure       $($r.Structure)"
    $report += "  Forge Load      $($r.ForgeLoad)"
    $report += "  Unsupported     $($r.Unsupported)"
    $report += "  AI warnings     $($r.AIWarnings)"
    $report += ""
}

$report += "=== INTERPRETATION ==="
$report += "PASS = deck loaded and Forge started a simulation."
$report += "Unsupported = Forge could not resolve the requested card from its card database."
$report += "AI warnings = warnings observed during this simulation only; they do not prove every possible AI path is unsupported."
$report += "This audit is diagnostic, not a gameplay benchmark."

$report | Set-Content -LiteralPath $txtPath -Encoding UTF8

Write-Host "============================================================"
Write-Host "AUDIT COMPLETE"
Write-Host "============================================================"
Write-Host "Summary: $txtPath"
Write-Host "CSV:     $csvPath"
Write-Host "Logs:    $LogDir"
Write-Host ""
$Results | Format-Table -AutoSize
