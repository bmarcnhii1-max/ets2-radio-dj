# Build + (re)install the ETS2 Radio DJ Game Bar widget
# Usage:  powershell -ExecutionPolicy Bypass -File build_widget.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj   = Join-Path $root "RadioWidget\RadioWidget.csproj"
$packageRoot = Join-Path $root "RadioWidget\AppPackages"
$registeredLayout = Join-Path $packageRoot "RadioWidget_Registered"
$msbuild = (Get-Command msbuild.exe -ErrorAction SilentlyContinue).Source
if (-not $msbuild) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $install = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
        if ($install) {
            $candidate = Join-Path $install "MSBuild\Current\Bin\MSBuild.exe"
            if (Test-Path $candidate) { $msbuild = $candidate }
        }
    }
}
if (-not $msbuild) {
    Write-Error "MSBuild not found. Install Visual Studio 2022 + 'Universal Windows Platform development' workload."
    exit 1
}

Write-Output "=== 1/3 Restore ==="
$widgetDir = Split-Path -Parent $proj
Set-Location $widgetDir
& $msbuild $proj /t:Restore /p:Configuration=Release /p:Platform=x64 /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Restore failed"; exit 1 }

Write-Output "=== 2/3 Build ==="
& $msbuild $proj /t:Build /p:Configuration=Release /p:Platform=x64 /p:AppxBundle=Never /v:m
if ($LASTEXITCODE -ne 0) { Write-Error "Build failed"; exit 1 }

Write-Output "=== 3/3 Register in Windows ==="
# Убить запущенный процесс виджета — иначе Game Bar держит старые файлы
# и после переустановки рисуется прежняя версия
Stop-Process -Name "RadioWidget" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
$pkg = Get-AppxPackage -Name RadioWidget -ErrorAction SilentlyContinue
if ($pkg) {
    Remove-AppxPackage -Package $pkg.PackageFullName -Confirm:$false
}

$kitBin = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$makeAppxCandidates = @(Get-ChildItem -LiteralPath $kitBin -Recurse -Filter "makeappx.exe" -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending)
if ($makeAppxCandidates.Count -eq 0) {
    Write-Error "makeappx.exe not found. Install the Windows SDK."
    exit 1
}
$makeAppx = $makeAppxCandidates[0].FullName

$msixCandidates = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -Filter "RadioWidget_*.msix" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
if ($msixCandidates.Count -eq 0) {
    Write-Error "AppX package was not generated."
    exit 1
}
$msix = $msixCandidates[0].FullName
if (Test-Path -LiteralPath $registeredLayout) {
    Remove-Item -LiteralPath $registeredLayout -Recurse -Force
}
& $makeAppx unpack /p $msix /d $registeredLayout
if ($LASTEXITCODE -ne 0) { Write-Error "AppX unpack failed"; exit 1 }
$registeredManifest = Join-Path $registeredLayout "AppxManifest.xml"
if (-not (Test-Path -LiteralPath $registeredManifest)) {
    Write-Error "Registered package manifest was not created."
    exit 1
}
Add-AppxPackage -Register $registeredManifest -ForceApplicationShutdown
Write-Output ""
Write-Output "DONE. Open Game Bar with Win+G -> widgets button -> 'ETS2 Radio DJ' -> pin it."
Write-Output "Make sure the radio server is running (start_radio.bat)."
