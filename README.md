# HandMouse

Control Windows with one hand and a webcam. No extra hardware.

Index tip moves the cursor. Pinches click, drag and scroll. Poses minimize, close and switch windows. A sideways hand enters Ctrl mode for copy, paste, undo.

**Tech page:** `docs/index.html` (enable GitHub Pages on `/docs`).
**Demo shot list:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

## Gestures

| Gesture | Action |
|---|---|
| Point (index) | Move cursor |
| Index only, others folded | Slow precise cursor |
| Thumb + middle tap | Left click |
| Double tap | Right click |
| Pinch hold | Drag |
| Tap + hold | Scroll |
| Thumb + pinky | Next monitor |
| Fist | Minimize window under cursor |
| Rock sign → open hand | Alt+Tab switcher, pick window |
| Flat palm + curl, 0.5 s | Close window under cursor |
| Thumbs-up | Freeze cursor · hold 2 s = Ctrl lock |
| Ctrl pose + fingers up, thumb tucked | Ctrl+C |
| Ctrl pose + index only | Ctrl+V |
| Ctrl pose + pinky only | Ctrl+Z |
| Ctrl pose + thumb straight up | Ctrl+D |
| Ctrl pose + rock sign | Enter |
| Ctrl pose + rock sign, hold 2 s | Window switcher (works in Ctrl lock) |
| Ctrl lock + fist / flat + curl | Minimize / close still work |

## Install (Windows 10/11, Python 3.10+ from python.org)

```bash
setup.bat
```

Makes `.venv`, installs `requirements.txt`, downloads `hand_landmarker.task` (the MediaPipe hand model) and unpacks the Gumball sprites into `gumball\sprites_v2`. Safe to run twice.

## Run

```bash
handmouse.bat --calibrate
handmouse.bat --ctrl
handmouse.bat
```

`--calibrate` fits the active box to your camera, `--ctrl` teaches your sideways Ctrl pose. Both write local files (`box.json`, `ctrl.json`) that are not shared. Skip them to use the defaults.

`--view` = preview only. `--test` = self-check, no webcam. Esc quits.

## Gumball

A desktop pet that replaces the screen borders. It starts and stops with the hand mouse (`pet.py`, its own process, click-through).

- Stands on the active window or the taskbar, rides the window when you drag it, falls if it's minimized, jumps or slides to the monitor your cursor is on.
- Eyes follow the cursor.
- Reacts to your gestures: drag, scroll, Ctrl mode, Ctrl lock (charges up during the 2 s thumbs-up), Ctrl+C/V/D, the window switcher, hand near the camera edge, hand lost.
- Dances while Apple Music plays, watches (seated) while a browser plays media, flexes on the other monitor while a game runs.
- Sits after 12 s still, sleeps after 2 min.
- Calm on purpose: reacts after 0.3 s, and waits longer the more gestures you chained in the last 4 s, so a quick Ctrl+D, Enter shows only where it ends.

Settings sit at the top of `pet.py`: `GAMES` (exe names that count as a game), `SIZE`, `SIT_AFTER`, `SLEEP_AFTER`, and the reaction timings. Every gesture is also logged with its time in `gestures.log` (not committed).

**Sprites.** `assets/gumball_sprites.zip` holds the animation frames (the animations came from reference images of a Cartoon Network character). They are for private use and are **not** covered by the MIT license. Keep this repository private. Use another folder with `set GUMBALL_SPRITES=C:\path\to\sprites_v2`. After new sprites arrive in `gumball\sprites_v2`, run `.venv\Scripts\python.exe pack_sprites.py`, then commit and push (each new zip adds ~50 MB to the clone, so repack rarely).

## License

MIT, for the code only (not the Gumball sprites).
