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
| Ctrl pose + rock sign, still 2 s | Enter |
| Ctrl pose + rock sign, move | Window switcher (works in Ctrl lock) |

## Install (Windows, Python 3.10+)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
curl -L -o hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

## Run

```bash
handmouse.bat --calibrate
handmouse.bat --ctrl
handmouse.bat
```

`--view` = preview only. `--test` = self-check, no webcam. Esc quits.

## License

MIT
