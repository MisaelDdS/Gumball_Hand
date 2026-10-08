# Records both monitors + the handmouse preview on a background -> Videos\handmouse_demo_<time>.mp4. Press q to stop.
# Layout knobs (canvas 1920x1080, each screen scaled to 864x486)
$bg  = "$env:USERPROFILE\Pictures\handmouse_bg_1080.png"
$out = "$env:USERPROFILE\Videos\handmouse_demo_$(Get-Date -f yyyyMMdd_HHmmss).mp4"
$ff  = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ff) { $ff = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe | select -First 1 -Expand FullName }
$dur = if ($args[0]) { @('-t', $args[0]) } else { @() }  # optional seconds, for a test clip

$filter = "[0:v]null[bg];" +
  "[1:v]split[a][b];[a]crop=1920:1080:0:0,scale=864:486[l];[b]crop=1920:1080:1920:0,scale=864:486[r];" +
  "[2:v]scale=360:-2[cam];" +
  "[bg][l]overlay=72:165[t1];[t1][r]overlay=984:165[t2];[t2][cam]overlay=780:720:shortest=1,format=yuv420p[v]"

& $ff -hide_banner -loglevel warning -stats `
  -loop 1 -framerate 30 -i $bg `
  -f gdigrab -framerate 30 -draw_mouse 1 -i desktop `
  -f gdigrab -framerate 30 -i title=handmouse `
  -filter_complex $filter -map "[v]" @dur `
  -c:v libx264 -preset ultrafast -crf 23 -movflags +faststart $out
Write-Host "saved $out"
