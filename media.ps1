# Media watcher for pet.py: every second prints one line, tab-separated "app|title" for each session that is Playing.
# Windows PowerShell 5.1 (WinRT access); pwsh 7 can't load WinRT types.
param([int]$Parent)  # pet.py's pid: exit when it's gone
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$M = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ($M::RequestAsync()) $M
while (-not $Parent -or (Get-Process -Id $Parent -ErrorAction SilentlyContinue)) {
    $out = foreach ($s in $mgr.GetSessions()) {
        if ($s.GetPlaybackInfo().PlaybackStatus -eq 'Playing') { $s.SourceAppUserModelId }
    }
    [Console]::Out.WriteLine((@($out) -join "`t"))
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 1000
}
