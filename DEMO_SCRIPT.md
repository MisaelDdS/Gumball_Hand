# Demo recording script (~90 s)

Setup before recording:
- Clean Windows desktop (new virtual desktop: Win+Ctrl+D). No personal folders.
- Brave private windows open: apple.com, instagram.com (logged out), wikipedia.org.
- Notepad open (for Ctrl shortcuts).
- Run `handmouse.bat`. Widget visible top-right.
- Recorder: Win+Alt+R (Game Bar) or OBS. Webcam overlay in a corner helps viewers.
- Good light on hand. Right hand only.

| # | Time | Gesture | Do on screen | Expected result |
|---|------|---------|--------------|-----------------|
| 1 | 0:00 | Open hand, point | Move cursor around apple.com | Smooth cursor follows index tip |
| 2 | 0:08 | Index only (others folded) | Hover a small link | Slow precise cursor |
| 3 | 0:13 | Thumb + middle tap | Click an Apple menu link | Left click, page opens |
| 4 | 0:20 | Thumb + middle tap, tap+hold, move hand | Scroll down the page | Page scrolls |
| 5 | 0:28 | Thumb + middle double tap | On a link | Right-click menu. Close it with a single tap elsewhere |
| 6 | 0:34 | Thumb + middle hold, move | Drag Notepad window by title bar | Window moves, releases on unpinch |
| 7 | 0:42 | Rock sign (index + pinky + thumb out) | Hold | Alt+Tab switcher opens |
| 8 | 0:45 | Point at a window, open hand | Pick Instagram | Instagram comes front |
| 9 | 0:52 | Thumb + pinky pinch (inside box) | Hold briefly | Cursor jumps to next monitor (skip if single monitor) |
| 10 | 0:56 | Fist | Over Wikipedia window | Window minimizes |
| 11 | 1:02 | Thumbs-up | Short | Cursor freezes, widget shows it |
| 12 | 1:06 | Hand on its side (Ctrl pose) | Over Notepad text | Ctrl mode on widget |
| 13 | 1:09 | Ctrl: fingers up, thumb tucked | Text selected first | Ctrl+C |
| 14 | 1:13 | Ctrl: only index up | | Ctrl+V pastes |
| 15 | 1:17 | Ctrl: only pinky up | | Ctrl+Z undoes |
| 16 | 1:21 | Ctrl: rock sign | | Enter |
| 17 | 1:25 | Back to open hand neutral, then upright | | Back to normal cursor |
| 18 | 1:28 | Flat palm down + curl fingertips, hold 0.5 s | Over a spare window | Window closes |
| 19 | 1:33 | Thumbs-up, wave | End shot | Freeze, clean outro |

Tips:
- Return to neutral (open hand) between every gesture. Avoids chained misfires.
- Pause ~1 s per gesture. Viewers need time to see it.
- Do one dry run first. Re-run `--calibrate` if the cursor can't reach edges.
- Skip #18 on any window with unsaved work.
- Cut misses in editing. Keep final clip under 90 s for LinkedIn autoplay.
