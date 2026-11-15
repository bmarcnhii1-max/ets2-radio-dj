# ETS2 Radio DJ — установщик v2 (WinForms, стиль сайта: тёмный + зелёный градиент)
# Все внешние процессы запускаются скрыто. Компиляция: PS2EXE -noConsole.
# Режимы: обычный запуск = GUI; "--silent" = консольный (для автоматизации).
param([string]$Mode = "")

$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$C_BG      = [System.Drawing.Color]::FromArgb(18, 18, 18)
$C_CARD    = [System.Drawing.Color]::FromArgb(24, 24, 24)
$C_CARD2   = [System.Drawing.Color]::FromArgb(36, 36, 36)
$C_TEXT    = [System.Drawing.Color]::FromArgb(255, 255, 255)
$C_MUTED   = [System.Drawing.Color]::FromArgb(196, 200, 208)
$C_DIM     = [System.Drawing.Color]::FromArgb(111, 116, 125)
$C_ACCENT  = [System.Drawing.Color]::FromArgb(29, 185, 84)
$C_ACCENT2 = [System.Drawing.Color]::FromArgb(10, 88, 42)
$C_YELLOW  = [System.Drawing.Color]::FromArgb(245, 166, 35)
$C_RED     = [System.Drawing.Color]::FromArgb(255, 92, 92)
$BUNDLE_URL = "https://github.com/bmarcnhii1-max/ets2-radio-dj/releases/latest/download/ETS2RadioDJ_bundle.zip"
$STREAM_ENTRY = "http://127.0.0.1:5000/radio.mp3|YT Automix DJ|Custom|RU|128|1"

# Все внешние процессы — скрыто, без вылезающих консолей
function Run-Hidden($exe, $argString) {
    $p = Start-Process -FilePath $exe -ArgumentList $argString -WindowStyle Hidden -Wait -PassThru
    return $p.ExitCode
}

function Find-PythonCmd {
    foreach ($c in @("py", "python")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

function Invoke-StepPython {
    $py = Find-PythonCmd
    if (-not $py) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Run-Hidden "winget" "install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements" | Out-Null
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            $py = Find-PythonCmd
        }
    }
    if (-not $py) { throw "Python не найден. Установи с python.org и перезапусти установщик." }
    return $py
}

function Invoke-StepPip($py, $appDir) {
    $req = Join-Path $appDir "requirements.txt"
    if (-not (Test-Path $req)) { throw "requirements.txt не найден в $appDir" }
    $code = Run-Hidden $py ("-m pip install -r `"$req`" --quiet --disable-pip-version-check")
    if ($code -ne 0) { throw "pip завершился с ошибкой (код $code)" }
}

function Invoke-StepWidget($appDir) {
    Stop-Process -Name "RadioWidget" -Force -ErrorAction SilentlyContinue
    $manifest = Join-Path $appDir "RadioWidget\AppPackages\RadioWidget_Registered\AppxManifest.xml"
    if (-not (Test-Path $manifest)) { return "пакет виджета не найден — пропущено" }
    Add-AppxPackage -Register $manifest -ForceApplicationShutdown -ErrorAction SilentlyContinue
    if (Get-AppxPackage -Name RadioWidget -ErrorAction SilentlyContinue) { return "виджет установлен (Win+G)" }
    return "виджет не зарегистрировался (нужна Win10/11)"
}

function Invoke-StepSmtc($appDir) {
    $bridge = Join-Path $appDir "smtc_bridge.ps1"
    if (-not (Test-Path $bridge)) { return "мост не найден — пропущено" }
    $startup = [Environment]::GetFolderPath("Startup")
    $shortcut = Join-Path $startup "ETS2 Radio SMTC.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($shortcut)
    $sc.TargetPath = "powershell.exe"
    $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridge`""
    $sc.WorkingDirectory = $appDir
    $sc.Save()
    return "медиа-кнопки в автозапуске"
}

function Invoke-StepEts2 {
    $docRoots = @()
    $docs = Join-Path $env:USERPROFILE "Documents"
    if (Test-Path $docs) { $docRoots += $docs }
    if ($env:OneDrive) {
        $od = Join-Path $env:OneDrive "Documents"
        if (Test-Path $od) { $docRoots += $od }
    }
    $done = @()
    foreach ($root in $docRoots) {
        foreach ($g in @("Euro Truck Simulator 2", "American Truck Simulator")) {
            $sii = Join-Path $root (Join-Path $g "live_streams.sii")
            if (-not (Test-Path $sii)) { continue }
            $content = [System.IO.File]::ReadAllText($sii)
            if ($content.Contains($STREAM_ENTRY)) { $done += "$g — уже прописано"; continue }
            Copy-Item $sii "$sii.bak" -Force -ErrorAction SilentlyContinue
            $lines = [System.Collections.Generic.List[string]]([System.IO.File]::ReadAllLines($sii))
            $inserted = $false
            for ($i = 0; $i -lt $lines.Count; $i++) {
                if ($lines[$i] -match 'live_streams:\s*\(\s*(\d+)\s*,') {
                    $lines[$i] = [regex]::Replace($lines[$i], '(live_streams:\s*\(\s*)(\d+)', { param($m) $m.Groups[1].Value + ([int]$m.Groups[2].Value + 1) })
                    $lines.Insert($i + 1, "`t.stream_data[]: `"$STREAM_ENTRY`",")
                    $inserted = $true
                    break
                }
            }
            if (-not $inserted) {
                for ($i = $lines.Count - 1; $i -ge 0; $i--) {
                    if ($lines[$i] -match 'stream_data\[\]:') {
                        $lines.Insert($i + 1, "`t.stream_data[]: `"$STREAM_ENTRY`",")
                        $inserted = $true
                        break
                    }
                }
            }
            if ($inserted) {
                [System.IO.File]::WriteAllLines($sii, $lines)
                $done += "$g — прописано (бэкап .bak)"
            } else { $done += "$g — не нашёл куда вставить" }
        }
    }
    if (-not $done) { return "игра не найдена в Documents — пропиши позже кнопкой в настройках" }
    return ($done -join "; ")
}

function Invoke-StepStart($appDir) {
    $busy = Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $busy) {
        $bat = Join-Path $appDir "start_radio.bat"
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$bat`"" -WindowStyle Hidden
        Start-Sleep -Seconds 5
    }
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue) {
        return "сервер работает"
    }
    return "сервер не поднялся — запусти start_radio.bat вручную"
}

# ---------- SILENT-режим ----------
if ($Mode -eq "--silent") {
    $appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if (-not (Test-Path (Join-Path $appDir "server.py"))) {
        Write-Host "server.py рядом с установщиком не найден — скачай полный архив."
        exit 1
    }
    try { $py = Invoke-StepPython; Write-Host "OK 1 Python: $py" } catch { Write-Host "FAIL python: $_"; exit 1 }
    try { Invoke-StepPip $py $appDir; Write-Host "OK 2 зависимости" } catch { Write-Host "FAIL pip: $_"; exit 1 }
    Write-Host (Invoke-StepWidget $appDir)
    Write-Host (Invoke-StepSmtc $appDir)
    Write-Host (Invoke-StepEts2)
    Write-Host (Invoke-StepStart $appDir)
    exit 0
}

# ================= GUI =================
$STEP_NAMES = @(
    "Файлы проекта",
    "Python",
    "Зависимости (flask, yt-dlp, ytmusicapi)",
    "Виджет Xbox Game Bar",
    "Медиа-кнопки в автозапуск",
    "Радио в live_streams.sii игры",
    "Запуск сервера"
)

$form = New-Object System.Windows.Forms.Form
$form.Text = "ETS2 Radio DJ — установка"
$form.Size = New-Object System.Drawing.Size(700, 600)
$form.BackColor = $C_BG
$form.ForeColor = $C_TEXT
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false

# Шапка с зелёным градиентом
$hero = New-Object System.Windows.Forms.Panel
$hero.Location = New-Object System.Drawing.Point(0, 0)
$hero.Size = New-Object System.Drawing.Size(684, 108)
$hero.Add_Paint({
    param($s, $e)
    $rect = New-Object System.Drawing.Rectangle(0, 0, $hero.Width, $hero.Height)
    $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, $C_ACCENT, $C_ACCENT2, 0)
    $e.Graphics.FillRectangle($brush, $rect)
})
$form.Controls.Add($hero)

$heroLogo = New-Object System.Windows.Forms.Label
$heroLogo.Text = "♪"
$heroLogo.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
$heroLogo.ForeColor = [System.Drawing.Color]::FromArgb(16, 16, 16)
$heroLogo.Location = New-Object System.Drawing.Point(24, 26)
$heroLogo.Size = New-Object System.Drawing.Size(56, 56)
$hero.Controls.Add($heroLogo)

$heroTitle = New-Object System.Windows.Forms.Label
$heroTitle.Text = "ETS2 RADIO DJ"
$heroTitle.Font = New-Object System.Drawing.Font("Segoe UI", 17, [System.Drawing.FontStyle]::Bold)
$heroTitle.ForeColor = [System.Drawing.Color]::White
$heroTitle.Location = New-Object System.Drawing.Point(92, 26)
$heroTitle.Size = New-Object System.Drawing.Size(400, 34)
$hero.Controls.Add($heroTitle)

$heroSub = New-Object System.Windows.Forms.Label
$heroSub.Text = "Личное радио YouTube Music для Euro Truck Simulator 2"
$heroSub.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
$heroSub.ForeColor = [System.Drawing.Color]::FromArgb(226, 242, 232)
$heroSub.Location = New-Object System.Drawing.Point(94, 60)
$heroSub.Size = New-Object System.Drawing.Size(420, 20)
$hero.Controls.Add($heroSub)

# Папка установки
$dirLabel = New-Object System.Windows.Forms.Label
$dirLabel.Text = "ПАПКА"
$dirLabel.Font = New-Object System.Drawing.Font("Segoe UI", 8, [System.Drawing.FontStyle]::Bold)
$dirLabel.ForeColor = $C_DIM
$dirLabel.Location = New-Object System.Drawing.Point(22, 122)
$dirLabel.Size = New-Object System.Drawing.Size(60, 18)
$form.Controls.Add($dirLabel)

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $appDir "server.py"))) {
    $appDir = Join-Path $env:USERPROFILE "ETS2RadioDJ"
}
$dirLabel2 = New-Object System.Windows.Forms.Label
$dirLabel2.Text = $appDir
$dirLabel2.Font = New-Object System.Drawing.Font("Consolas", 10)
$dirLabel2.ForeColor = $C_TEXT
$dirLabel2.Location = New-Object System.Drawing.Point(22, 140)
$dirLabel2.Size = New-Object System.Drawing.Size(540, 20)
$dirLabel2.AutoEllipsis = $true
$form.Controls.Add($dirLabel2)

$dirBtn = New-Object System.Windows.Forms.Button
$dirBtn.Text = "Сменить"
$dirBtn.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$dirBtn.Location = New-Object System.Drawing.Point(575, 138)
$dirBtn.Size = New-Object System.Drawing.Size(92, 24)
$dirBtn.BackColor = $C_CARD2
$dirBtn.ForeColor = $C_TEXT
$dirBtn.FlatStyle = "Flat"
$dirBtn.FlatAppearance.BorderSize = 0
$dirBtn.Cursor = "Hand"
$form.Controls.Add($dirBtn)

# Шаги-чеклист
$stepLabels = @()
for ($i = 0; $i -lt $STEP_NAMES.Count; $i++) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = "○  " + $STEP_NAMES[$i]
    $l.Font = New-Object System.Drawing.Font("Segoe UI", 10.5)
    $l.ForeColor = $C_DIM
    $l.Location = New-Object System.Drawing.Point(28, (180 + $i * 30))
    $l.Size = New-Object System.Drawing.Size(630, 24)
    $form.Controls.Add($l)
    $stepLabels += $l
}

function Set-Step($idx, $state, $detail) {
    $l = $stepLabels[$idx]
    $name = $STEP_NAMES[$idx]
    switch ($state) {
        "active"   { $l.Text = "▸  $name"; $l.ForeColor = $C_TEXT; if ($detail) { $l.Text = "▸  $name — $detail" } }
        "done"     { $l.Text = "✓  $name"; $l.ForeColor = $C_ACCENT; if ($detail) { $l.Text = "✓  $name  ($detail)" } }
        "warn"     { $l.Text = "✓  $name"; $l.ForeColor = $C_YELLOW; if ($detail) { $l.Text = "✓  $name  ($detail)" } }
        "fail"     { $l.Text = "✕  $name"; $l.ForeColor = $C_RED; if ($detail) { $l.Text = "✕  $name  — $detail" } }
        default    { $l.Text = "○  $name"; $l.ForeColor = $C_DIM }
    }
}
for ($i = 0; $i -lt $STEP_NAMES.Count; $i++) { Set-Step $i "pending" $null }

# Детали текущего шага
$detail = New-Object System.Windows.Forms.Label
$detail.Text = ""
$detail.Font = New-Object System.Drawing.Font("Consolas", 9)
$detail.ForeColor = $C_MUTED
$detail.Location = New-Object System.Drawing.Point(48, 400)
$detail.Size = New-Object System.Drawing.Size(610, 40)
$form.Controls.Add($detail)

# Прогресс
$progBack = New-Object System.Windows.Forms.Panel
$progBack.Location = New-Object System.Drawing.Point(20, 455)
$progBack.Size = New-Object System.Drawing.Size(640, 8)
$progBack.BackColor = $C_CARD2
$form.Controls.Add($progBack)
$progFill = New-Object System.Windows.Forms.Panel
$progFill.Location = New-Object System.Drawing.Point(0, 0)
$progFill.Size = New-Object System.Drawing.Size(0, 8)
$progFill.BackColor = $C_ACCENT
$progBack.Controls.Add($progFill)

$status = New-Object System.Windows.Forms.Label
$status.Text = "Нажми «Установить» — остальное сделаю я."
$status.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$status.ForeColor = $C_MUTED
$status.Location = New-Object System.Drawing.Point(20, 472)
$status.Size = New-Object System.Drawing.Size(640, 20)
$form.Controls.Add($status)

# Кнопки
$installBtn = New-Object System.Windows.Forms.Button
$installBtn.Text = "УСТАНОВИТЬ"
$installBtn.Font = New-Object System.Drawing.Font("Segoe UI", 10.5, [System.Drawing.FontStyle]::Bold)
$installBtn.Location = New-Object System.Drawing.Point(20, 505)
$installBtn.Size = New-Object System.Drawing.Size(210, 46)
$installBtn.BackColor = $C_ACCENT
$installBtn.ForeColor = [System.Drawing.Color]::FromArgb(10, 10, 10)
$installBtn.FlatStyle = "Flat"
$installBtn.FlatAppearance.BorderSize = 0
$installBtn.Cursor = "Hand"
$form.Controls.Add($installBtn)

$openBtn = New-Object System.Windows.Forms.Button
$openBtn.Text = "Открыть приложение"
$openBtn.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$openBtn.Location = New-Object System.Drawing.Point(242, 505)
$openBtn.Size = New-Object System.Drawing.Size(210, 46)
$openBtn.BackColor = $C_CARD2
$openBtn.ForeColor = $C_TEXT
$openBtn.FlatStyle = "Flat"
$openBtn.FlatAppearance.BorderSize = 0
$openBtn.Cursor = "Hand"
$openBtn.Enabled = $false
$form.Controls.Add($openBtn)

$exitBtn = New-Object System.Windows.Forms.Button
$exitBtn.Text = "Выход"
$exitBtn.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
$exitBtn.Location = New-Object System.Drawing.Point(464, 505)
$exitBtn.Size = New-Object System.Drawing.Size(120, 46)
$exitBtn.BackColor = $C_CARD2
$exitBtn.ForeColor = $C_TEXT
$exitBtn.FlatStyle = "Flat"
$exitBtn.FlatAppearance.BorderSize = 0
$exitBtn.Cursor = "Hand"
$form.Controls.Add($exitBtn)
$exitBtn.Add_Click({ $form.Close() })
$openBtn.Add_Click({ Start-Process "http://127.0.0.1:5000" })

# ---------- Фоновая установка ----------
$sync = [hashtable]::Synchronized(@{})
$sync.Detail = ""
$sync.Progress = 0
$sync.Done = $false
$sync.Fail = $false
$sync.Steps = @{}
for ($i = 0; $i -lt $STEP_NAMES.Count; $i++) { $sync.Steps[$i] = @{state = "pending"; detail = ""} }

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    for ($i = 0; $i -lt $STEP_NAMES.Count; $i++) {
        $st = $sync.Steps[$i]
        Set-Step $i $st.state $st.detail
    }
    $progFill.Width = [int]([Math]::Min(1.0, [double]$sync.Progress) * $progBack.Width)
    if ($sync.Fail) { $status.ForeColor = $C_RED }
    elseif ($sync.Done) { $status.ForeColor = $C_ACCENT }
    else { $status.ForeColor = $C_MUTED }
    if ($sync.Detail) { $detail.Text = $sync.Detail }
    if ($sync.Done) {
        $timer.Stop()
        $installBtn.Enabled = $true
        $installBtn.Text = "ГОТОВО"
        if (-not $sync.Fail) {
            $openBtn.Enabled = $true
            $status.Text = "Установка завершена — приятной дороги!"
        } else {
            $status.Text = "Установка завершилась с ошибкой — смотри шаги выше."
        }
    }
})

$installBtn.Add_Click({
    $installBtn.Enabled = $false
    $appDir = $dirLabel2.Text
    $sync.Detail = ""
    $sync.Progress = 0
    $sync.Done = $false
    $sync.Fail = $false
    for ($i = 0; $i -lt $STEP_NAMES.Count; $i++) { $sync.Steps[$i] = @{state = "pending"; detail = ""} }

    $rs = [runspacefactory]::CreateRunspace()
    $rs.Open()
    $rs.SessionStateProxy.SetVariable("sync", $sync)
    $rs.SessionStateProxy.SetVariable("appDir", $appDir)
    $rs.SessionStateProxy.SetVariable("bundleUrl", $BUNDLE_URL)
    $rs.SessionStateProxy.SetVariable("streamEntry", $STREAM_ENTRY)
    $ps = [powershell]::Create()
    $ps.Runspace = $rs
    $ps.AddScript({
        function Mark($idx, $state, $detail) {
            $sync.Steps[$idx] = @{state = $state; detail = $detail}
            if ($state -eq "done" -or $state -eq "warn") { $sync.Progress = [Math]::Max($sync.Progress, ($idx + 1) / 7.0) }
        }
        function Run-Hidden($exe, $argString) {
            $p = Start-Process -FilePath $exe -ArgumentList $argString -WindowStyle Hidden -Wait -PassThru
            return $p.ExitCode
        }

        try {
            # 1. Файлы проекта
            Mark 0 "active" ""
            $localHere = Test-Path (Join-Path $appDir "server.py")
            if (-not $localHere) {
                $zip = Join-Path $env:TEMP "ETS2RadioDJ_bundle.zip"
                Invoke-WebRequest -Uri $bundleUrl -OutFile $zip -UseBasicParsing
                if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force -ErrorAction SilentlyContinue }
                New-Item -ItemType Directory -Path $appDir -Force | Out-Null
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $appDir)
                Mark 0 "done" "распаковано"
            } else {
                Mark 0 "done" "уже рядом с установщиком"
            }

            # 2. Python
            Mark 1 "active" ""
            $py = $null
            foreach ($c in @("py", "python")) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
            if (-not $py) {
                if (Get-Command winget -ErrorAction SilentlyContinue) {
                    Run-Hidden "winget" "install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements" | Out-Null
                    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
                    foreach ($c in @("py", "python")) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
                }
            }
            if (-not $py) { throw "Python не установлен — скачай с python.org и перезапусти" }
            Mark 1 "done" $py

            # 3. Зависимости
            Mark 2 "active" ""
            $req = '"' + (Join-Path $appDir "requirements.txt") + '"'
            $code = Run-Hidden $py ("-m pip install -r " + $req + " --quiet --disable-pip-version-check")
            if ($code -ne 0) { throw "pip завершился с ошибкой (код $code)" }
            Mark 2 "done" ""

            # 4. Виджет
            Mark 3 "active" ""
            Stop-Process -Name "RadioWidget" -Force -ErrorAction SilentlyContinue
            $manifest = Join-Path $appDir "RadioWidget\AppPackages\RadioWidget_Registered\AppxManifest.xml"
            if (Test-Path $manifest) {
                Add-AppxPackage -Register $manifest -ForceApplicationShutdown -ErrorAction SilentlyContinue
                if (Get-AppxPackage -Name RadioWidget -ErrorAction SilentlyContinue) { Mark 3 "done" "Win+G -> закрепи" }
                else { Mark 3 "warn" "не зарегистрировался" }
            } else { Mark 3 "warn" "пакет не найден" }

            # 5. SMTC
            Mark 4 "active" ""
            $bridge = Join-Path $appDir "smtc_bridge.ps1"
            if (Test-Path $bridge) {
                $startup = [Environment]::GetFolderPath("Startup")
                $shortcut = Join-Path $startup "ETS2 Radio SMTC.lnk"
                $wsh = New-Object -ComObject WScript.Shell
                $sc = $wsh.CreateShortcut($shortcut)
                $sc.TargetPath = "powershell.exe"
                $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$bridge`""
                $sc.WorkingDirectory = $appDir
                $sc.Save()
                Mark 4 "done" "автозапуск"
            } else { Mark 4 "warn" "мост не найден" }

            # 6. ETS2
            Mark 5 "active" ""
            $docRoots = @()
            $docs = Join-Path $env:USERPROFILE "Documents"
            if (Test-Path $docs) { $docRoots += $docs }
            if ($env:OneDrive) { $od = Join-Path $env:OneDrive "Documents"; if (Test-Path $od) { $docRoots += $od } }
            $any = $false
            $summary = @()
            foreach ($root in $docRoots) {
                foreach ($g in @("Euro Truck Simulator 2", "American Truck Simulator")) {
                    $sii = Join-Path $root (Join-Path $g "live_streams.sii")
                    if (-not (Test-Path $sii)) { continue }
                    $any = $true
                    $content = [System.IO.File]::ReadAllText($sii)
                    if ($content.Contains($streamEntry)) { $summary += "${g}: уже есть"; continue }
                    Copy-Item $sii "$sii.bak" -Force -ErrorAction SilentlyContinue
                    $lines = [System.Collections.Generic.List[string]]([System.IO.File]::ReadAllLines($sii))
                    $inserted = $false
                    for ($i = 0; $i -lt $lines.Count; $i++) {
                        if ($lines[$i] -match 'live_streams:\s*\(\s*(\d+)\s*,') {
                            $lines[$i] = [regex]::Replace($lines[$i], '(live_streams:\s*\(\s*)(\d+)', { param($m) $m.Groups[1].Value + ([int]$m.Groups[2].Value + 1) })
                            $lines.Insert($i + 1, "`t.stream_data[]: `"$streamEntry`",")
                            $inserted = $true
                            break
                        }
                    }
                    if (-not $inserted) {
                        for ($i = $lines.Count - 1; $i -ge 0; $i--) {
                            if ($lines[$i] -match 'stream_data\[\]:') {
                                $lines.Insert($i + 1, "`t.stream_data[]: `"$streamEntry`",")
                                $inserted = $true
                                break
                            }
                        }
                    }
                    if ($inserted) {
                        [System.IO.File]::WriteAllLines($sii, $lines)
                        $summary += "${g}: прописано"
                    } else { $summary += "${g}: не нашёл куда" }
                }
            }
            if ($any) { Mark 5 "done" ($summary -join "; ") }
            else { Mark 5 "warn" "игра не найдена — пропиши кнопкой в настройках" }

            # 7. Сервер
            Mark 6 "active" ""
            $busy = Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue
            if (-not $busy) {
                Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$(Join-Path $appDir 'start_radio.bat')`"" -WindowStyle Hidden
                Start-Sleep -Seconds 5
            }
            if (Test-NetConnection -ComputerName 127.0.0.1 -Port 5000 -InformationLevel Quiet -WarningAction SilentlyContinue) {
                Mark 6 "done" "http://127.0.0.1:5000"
            } else { Mark 6 "warn" "запусти start_radio.bat вручную" }

            $sync.Progress = 1
            $sync.Done = $true
        } catch {
            $sync.Detail = $_.Exception.Message
            $sync.Progress = 1
            $sync.Done = $true
            $sync.Fail = $true
        }
    })
    $ps.BeginInvoke() | Out-Null
    $timer.Start()
})

$form.Add_Shown({ $form.Activate() })
[System.Windows.Forms.Application]::Run($form)
