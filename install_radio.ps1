# ETS2 Radio DJ — установщик «одним запуском»
# Ставит зависимости Python, регистрирует виджет Game Bar из готового пакета,
# добавляет SMTC-мост в автозапуск и прописывает радио в ETS2/ATS.
# Запуск: правый клик -> Выполнить с PowerShell, или install_radio.bat
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step($n, $msg) { Write-Host ("[{0}/6] {1}" -f $n, $msg) -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !! $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "    XX $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "================ ETS2 RADIO DJ — УСТАНОВКА ================" -ForegroundColor Magenta
Write-Host ""

# ---------- 1. Python ----------
Step 1 "Проверка Python..."
$pyCmd = $null
foreach ($c in @("py", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $pyCmd = $c; break }
}
if (-not $pyCmd) {
    Warn "Python не найден — ставлю через winget (Python 3.12)..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements | Out-Null
        # обновить PATH в текущей сессии
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        foreach ($c in @("py", "python")) {
            if (Get-Command $c -ErrorAction SilentlyContinue) { $pyCmd = $c; break }
        }
    } else {
        Fail "winget не найден. Открываю страницу Python — установи и запусти установщик заново."
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
}
if (-not $pyCmd) { Fail "Python так и не появился. Установи вручную и перезапусти."; exit 1 }
$ver = & $pyCmd --version 2>&1
Ok "Python: $ver"

# ---------- 2. Зависимости ----------
Step 2 "Установка Python-зависимостей (flask, ytmusicapi, yt-dlp, pypresence)..."
if (-not (Test-Path (Join-Path $root "requirements.txt"))) {
    Fail "requirements.txt не найден рядом с установщиком."
    exit 1
}
& $pyCmd -m pip install -r (Join-Path $root "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "pip завершился с ошибкой. Проверь интернет и запусти заново."; exit 1 }
Ok "Зависимости установлены"

# ---------- 3. Виджет Game Bar (из готового пакета, MSBuild не нужен) ----------
Step 3 "Регистрация виджета Game Bar..."
Stop-Process -Name "RadioWidget" -Force -ErrorAction SilentlyContinue
$manifest = Join-Path $root "RadioWidget\AppPackages\RadioWidget_Registered\AppxManifest.xml"
if (Test-Path $manifest) {
    Add-AppxPackage -Register $manifest -ForceApplicationShutdown -ErrorAction SilentlyContinue
    if (Get-AppxPackage -Name RadioWidget -ErrorAction SilentlyContinue) {
        Ok "Виджет установлен (Win+G -> ETS2 Radio DJ -> закрепи)"
    } else {
        Warn "Не удалось зарегистрировать виджет. Проверь Windows (нужна Win10/11 с Game Bar)."
    }
} else {
    Warn "Готовый пакет виджета не найден (RadioWidget\AppPackages\RadioWidget_Registered). Пропускаю."
}

# ---------- 4. SMTC-мост в автозапуск ----------
Step 4 "SMTC-мост (кнопки медиа/Play в Game Bar) в автозапуск..."
$bridge = Join-Path $root "smtc_bridge.ps1"
if (Test-Path $bridge) {
    $startup = [Environment]::GetFolderPath("Startup")
    $shortcut = Join-Path $startup "ETS2 Radio SMTC.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($shortcut)
    $sc.TargetPath = "powershell.exe"
    $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridge`""
    $sc.WorkingDirectory = $root
    $sc.Save()
    Ok "Ярлык автозапуска: $shortcut"
} else {
    Warn "smtc_bridge.ps1 не найден — пропускаю."
}

# ---------- 5. Радио в ETS2/ATS ----------
Step 5 "Прописываю радио в live_streams.sii игры..."
$entry = "http://127.0.0.1:5000/radio.mp3|YT Automix DJ|Custom|RU|128|1"
$docRoots = @()
$docs = Join-Path $env:USERPROFILE "Documents"
if (Test-Path $docs) { $docRoots += $docs }
if ($env:OneDrive) {
    $od = Join-Path $env:OneDrive "Documents"
    if (Test-Path $od) { $docRoots += $od }
}
$games = @("Euro Truck Simulator 2", "American Truck Simulator")
$done = $false
foreach ($root2 in $docRoots) {
    foreach ($g in $games) {
        $sii = Join-Path $root2 (Join-Path $g "live_streams.sii")
        if (-not (Test-Path $sii)) { continue }
        $done = $true
        $content = Get-Content $sii -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($content -and $content.Contains($entry)) {
            Ok "$g — радио уже прописано ($sii)"
            continue
        }
        Copy-Item $sii "$sii.bak" -Force -ErrorAction SilentlyContinue
        $lines = [System.Collections.Generic.List[string]]($content -split "`r?`n")
        $inserted = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match 'live_streams:\s*\(\s*(\d+)\s*,') {
                $lines[$i] = $lines[$i] -replace 'live_streams:\s*\(\s*(\d+)', { param($m) "live_streams: ($([int]$m.Groups[1].Value + 1)" }
                $lines.Insert($i + 1, "`t.stream_data[]: `"$entry`",")
                $inserted = $true
                break
            }
        }
        if (-not $inserted) {
            for ($i = $lines.Count - 1; $i -ge 0; $i--) {
                if ($lines[$i] -match 'stream_data\[\]:') {
                    $lines.Insert($i + 1, "`t.stream_data[]: `"$entry`",")
                    $inserted = $true
                    break
                }
            }
        }
        if ($inserted) {
            Set-Content -Path $sii -Value ($lines -join "`r`n") -Encoding UTF8
            Ok "$g — радио прописано ($sii), бэкап: $sii.bak"
        } else {
            Warn "$g — не нашёл куда вставить строку. Вставь вручную: $sii"
        }
    }
}
if (-not $done) {
    Warn "Файлы игры не найдены в Documents (ETS2/ATS). Запусти игру один раз — она создаст папку — и нажми «Прописать» в Web UI (Настройки)."
}

# ---------- 6. Запуск ----------
Step 6 "Запуск радио-сервера..."
$portBusy = Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue
if (-not $portBusy) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$root\start_radio.bat`"" -WindowStyle Minimized
    Start-Sleep -Seconds 4
}
if (Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue) {
    Ok "Сервер работает: http://127.0.0.1:5000"
} else {
    Warn "Сервер не поднялся — запусти start_radio.bat вручную и посмотри ошибки."
}

Write-Host ""
Write-Host "================ ГОТОВО ================" -ForegroundColor Magenta
Write-Host "  Веб-интерфейс : http://127.0.0.1:5000" -ForegroundColor White
Write-Host "  В игре        : Radio -> Custom -> YT Automix DJ" -ForegroundColor White
Write-Host "  Game Bar      : Win+G -> ETS2 Radio DJ -> закрепи" -ForegroundColor White
Write-Host ""
Read-Host "Нажми Enter для закрытия"
