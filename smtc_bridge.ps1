# ETS2 Radio DJ - SMTC Bridge
# Registers our radio into Windows System Media Transport Controls.
# Gives Prev/Play/Pause/Next buttons in Game Bar media widget + media keys.
# Requires: Windows 10/11 (WinRT built-in), PowerShell 5.1+, server on :5000.

$ErrorActionPreference = "Continue"
$mutex = New-Object System.Threading.Mutex($false, "Local\ETS2RadioSmtcBridge")
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
$API = "http://127.0.0.1:5000"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NativeKeys {
    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int vKey);
    public static bool Down(int vKey) { return (GetAsyncKeyState(vKey) & 0x8000) != 0; }
}
'@
Add-Type -AssemblyName System.Runtime.WindowsRuntime
# --- Load WinRT types ---
$null = [Windows.Media.Playback.MediaPlayer, Windows.Media.Playback, ContentType=WindowsRuntime]
$null = [Windows.Media.SystemMediaTransportControls, Windows.Media, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.RandomAccessStreamReference, Windows.Storage.Streams, ContentType=WindowsRuntime]
Write-Output "[SMTC] WinRT types loaded"

# --- Create MediaPlayer and grab SMTC ---
$mp = New-Object Windows.Media.Playback.MediaPlayer
$smtc = $mp.SystemMediaTransportControls
$smtc.IsEnabled = $true
$smtc.IsPlayEnabled = $true
$smtc.IsPauseEnabled = $true
$smtc.IsNextEnabled = $true
$smtc.IsPreviousEnabled = $true
$smtc.IsRewindEnabled = $true
$smtc.IsFastForwardEnabled = $true
$smtc.PlaybackStatus = [Windows.Media.MediaPlaybackStatus]::Stopped

$updater = $smtc.DisplayUpdater
$updater.Type = [Windows.Media.MediaPlaybackType]::Music
$updater.MusicProperties.Title = "ETS2 Radio DJ"
$updater.MusicProperties.Artist = "Starting..."
$updater.Update()
Write-Output "[SMTC] Controls initialized"

function Invoke-Control($action, $time = $null) {
    try {
        $body = @{ action = $action }
        if ($null -ne $time) { $body.time = $time }
        $json = $body | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "$API/api/control" -Method POST -Body $json -ContentType "application/json" -TimeoutSec 8 | Out-Null
    } catch {}
}

$script:lastTime = 0
$script:hotkeyState = @{}

function Trigger-Hotkey($name, $pressed, $action, $time = $null) {
    if ($pressed -and -not $script:hotkeyState[$name]) {
        Invoke-Control $action $time
    }
    $script:hotkeyState[$name] = $pressed
}

function Update-Hotkeys {
    $modifiers = [NativeKeys]::Down(0x11) -and [NativeKeys]::Down(0x12)
    Trigger-Hotkey "pause" ($modifiers -and [NativeKeys]::Down(0x20)) "pause"
    Trigger-Hotkey "next" ($modifiers -and [NativeKeys]::Down(0x27)) "skip"
    Trigger-Hotkey "previous" ($modifiers -and [NativeKeys]::Down(0x25)) "previous"
}

# --- Button handler ---
$buttonHandler = {
    param($sender, $args)
    $btn = $args.Button.ToString()
    Write-Output "[SMTC] Button: $btn"
    switch ($btn) {
        "Play"        { Invoke-Control "pause" }
        "Pause"       { Invoke-Control "pause" }
        "Next"        { Invoke-Control "skip" }
        "Previous"    { Invoke-Control "previous" }
        "Rewind"      { Invoke-Control "seek" ([Math]::Max(0, $script:lastTime - 15)) }
        "FastForward" { Invoke-Control "seek" ($script:lastTime + 15) }
    }
}
$smtc.add_ButtonPressed($buttonHandler)
Write-Output "[SMTC] Button handler attached"

Write-Output "[SMTC] Bridge running. Polling $API every second. Ctrl+C to exit."

# --- Main loop: sync status/metadata ---
$lastTitle = ""
while ($true) {
    Update-Hotkeys
    try {
        $resp = Invoke-RestMethod -Uri "$API/api/status" -Method GET -TimeoutSec 5
        $track = $resp.track
        $script:lastTime = [int]$track.current_time

        if ($track.is_paused) {
            $smtc.PlaybackStatus = [Windows.Media.MediaPlaybackStatus]::Paused
        } else {
            $smtc.PlaybackStatus = [Windows.Media.MediaPlaybackStatus]::Playing
        }

        $title = [string]$track.title
        if ($title -ne $lastTitle) {
            $lastTitle = $title
            $updater.MusicProperties.Title = $title
            $updater.MusicProperties.Artist = [string]$track.artist
            $updater.MusicProperties.AlbumTitle = [string]$track.album
            if ($track.thumbnail) {
                try {
                    $uri = New-Object System.Uri([string]$track.thumbnail)
                    $updater.Thumbnail = [Windows.Storage.Streams.RandomAccessStreamReference]::CreateFromUri($uri)
                } catch {}
            }
            $updater.Update()
            Write-Output "[SMTC] Track: $title - $($track.artist)"
        }

        $timeline = New-Object Windows.Media.SystemMediaTransportControlsTimelineProperties
        $timeline.StartTime = [TimeSpan]::Zero
        $timeline.Position = [TimeSpan]::FromSeconds([int]$track.current_time)
        $timeline.EndTime = [TimeSpan]::FromSeconds([int]$track.duration)
        $smtc.UpdateTimelineProperties($timeline)

    } catch {
        $smtc.PlaybackStatus = [Windows.Media.MediaPlaybackStatus]::Stopped
    }
    Start-Sleep -Milliseconds 500
}
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
