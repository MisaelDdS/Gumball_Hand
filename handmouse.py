"""Hand mouse: index tip = cursor, thumb+middle
pinch = clicks, thumb+pinky inside the box = next monitor, fist = minimize window under cursor, rock sign (index + pinky + thumb out) = window switcher:
point the cursor at a window, raise middle + ring (open hand) to open it; any other pose cancels.
Close: tip the hand flat (palm down) and curl the fingertips to the bottom of the palm, held CLOSE_HOLD s =
close the window under the cursor. Tipping the hand flat alone freezes the cursor.
Red pulse on the active monitor's edge = your index tip left the box on that side.
Thumbs-up (all four fingers folded, thumb out) = cursor and gestures freeze, so you can pull the hand away.
Thumbs-up held LOCK_HOLD s = Ctrl lock on/off: Ctrl mode stays on with the normal upright hand, no cursor.
While locked, Ctrl+D fires when a short thumbs-up is released (holding it is the unlock).
Ctrl mode: hold the hand angle captured by --ctrl. Cursor and clicks pause; from neutral,
fingers up + thumb tucked across the palm = Ctrl+C, only index up = Ctrl+V, only pinky up = Ctrl+Z,
only thumb straight up = Ctrl+D, rock sign held still ENTER_HOLD s = Enter (no Ctrl);
rock sign moved = window switcher, cursor live only while it is open. Back to neutral (open hand, thumb out) to re-arm.

python handmouse.py          control the mouse + preview window
python handmouse.py --view   preview only, mouse untouched
python handmouse.py --test   gesture self-check, no webcam
python handmouse.py --calibrate   point (index only) and sweep your zone, open hand to save -> box.json
python handmouse.py --ctrl        3 s countdown, then hold your Ctrl pose 5 s -> ctrl.json
Esc in the preview window (or Ctrl+C in the terminal) quits.
"""
import ctypes
import json
import math
import sys
import time
from collections import deque, namedtuple
from ctypes import wintypes
from pathlib import Path

# ---- tuning knobs ----
CAM_INDEX = 0
PINCH_ON, PINCH_OFF = 0.30, 0.40  # fingertip gap / hand size; hysteresis stops flicker
HOLD = 0.25          # s pinched before it counts as hold (drag/scroll)
DOUBLE = 0.30        # s window for the second tap
EURO_MIN_CUTOFF = 0.8  # Hz: smoothing when the hand is still; lower = steadier but laggier
EURO_BETA = 6.0        # how fast smoothing lets go when the hand moves; higher = less lag on fast moves
EURO_D_CUTOFF = 1.0    # Hz: smoothing of the speed estimate itself
FINE_SPEED = 0.3       # index + thumb out, other fingers folded: cursor moves at this fraction of speed
CATCHUP = 6.0          # after fine mode, normal movement eases the cursor back onto your hand this fast
BOX = (0.50, 0.05, 0.95, 0.50)  # left, top, right, bottom (frame fractions) mapped to the monitor:
                                # top-right quarter, padded off the top/right camera edges
BOX_FILE = Path(__file__).with_name("box.json")
if BOX_FILE.exists():  # written by --calibrate, wins over the default above
    BOX = tuple(json.loads(BOX_FILE.read_text()))
CAL_TRIM = 0.02      # calibration ignores this fraction of extreme points on each side (stray jumps)
SCROLL_GAIN = 3000   # wheel units per full frame height of hand movement
POSE_HOLD = 0.30     # s a fist / rock pose must hold before it fires
FIST_LOOKBACK = 0.30 # s: minimize the window the cursor was on before the hand began curling
THUMB_OUT = 0.60     # thumb tip to ring knuckle / hand size; above = thumb out
SWITCH_LOOKBACK = 0.25  # s: the switcher click lands where the cursor was before leaving the rock sign
FREEZE_LOOKBACK = 0.15  # s: on thumbs-up, snap the cursor back to before closing the hand nudged it

LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, WHEEL = 0x2, 0x4, 0x8, 0x10, 0x800
KEYS = {"ctrl": 0x11, "alt": 0x12, "tab": 0x09, "left": 0x25, "right": 0x27, "enter": 0x0D, "esc": 0x1B,
        "c": 0x43, "v": 0x56, "w": 0x57, "shift": 0x10}
THUMB_STRAIGHT = 0.92  # Ctrl+D: thumb base-to-tip / joint path; 1 = dead straight, bent < this
THUMB_HOLD = 0.25    # s a recorded move must hold before it fires (skips poses passed through)
LOCK_HOLD = 2.0      # s of thumbs-up that toggles Ctrl lock
CTRL_FILE = Path(__file__).with_name("ctrl.json")  # Ctrl pose captured by --ctrl
CLOSE_HOLD = 0.5     # s flat + curled must hold before the window closes (it can't be undone)
FLAT_RATIO = 1.0     # palm width / palm length above this = hand tipped flat (yours: 1.3-1.5 flat, <0.75 upright)
CURL_REACH = 1.05    # fingertip-to-wrist / palm width below this = curled (yours: ~0.85 curled, ~1.3 flat-open)
CTRL_MIN_MATCH = 0.30  # floor for the captured threshold: thumb moves and fists shift the knuckles to ~0.22
CTRL_LOOSEN = 1.0    # multiply the captured threshold: >1 = Ctrl mode easier to enter, <1 = stricter
CTRL_EXIT = 0.15     # s off the Ctrl pose before Ctrl mode ends (a few frames, so one bad frame doesn't drop it)
CTRL_LOOKBACK = 0.3  # s: on entering Ctrl mode, snap the cursor back to before the hand started turning
ENTER_HOLD = 2.0     # s Ctrl-mode rock sign must hold still before Enter fires
ROCK_STILL = 0.04    # box fraction the index may drift during that hold; more = window switcher instead
SHELL = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # never minimize these

# MediaPipe hand landmark ids
WRIST, THUMB, INDEX_MCP, INDEX, MID_MCP, MIDDLE, RING_MCP, PINKY = 0, 4, 5, 8, 9, 12, 13, 20
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]  # (tip, middle joint) for index..pinky
PALM = (5, 9, 13, 17)  # knuckles: they stay put when fingers fold, so they capture hand angle only
Pt = namedtuple("Pt", "x y")  # recorded landmark, same .x/.y shape as MediaPipe's
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


class Gestures:
    """Thumb+middle pinch stream -> click/drag/scroll events. Pure, no I/O."""

    def __init__(self):
        self.state, self.t0 = "IDLE", 0.0

    def update(self, t, pinched):
        s = self.state
        if s == "IDLE" and pinched:
            self.state, self.t0 = "DOWN1", t
        elif s == "DOWN1":
            if not pinched:
                self.state, self.t0 = "UP1", t
            elif t - self.t0 > HOLD:
                self.state = "DRAG"
                return ["drag_start"]
        elif s == "UP1":
            if pinched:
                self.state, self.t0 = "DOWN2", t
            elif t - self.t0 > DOUBLE:
                self.state = "IDLE"
                return ["left_click"]
        elif s == "DOWN2":
            if not pinched:
                self.state = "IDLE"
                return ["right_click"]
            if t - self.t0 > HOLD:
                self.state = "SCROLL"
                return ["scroll_start"]
        elif s in ("DRAG", "SCROLL") and not pinched:
            self.state = "IDLE"
            return [s.lower() + "_end"]
        return []

    def reset(self):
        """Hand lost: drop pending taps, never leave the button held."""
        was, self.state = self.state, "IDLE"
        return ["drag_end"] if was == "DRAG" else []

    @property
    def frozen(self):
        # hold the cursor still while a click is being formed so the pinch doesn't nudge it
        return self.state in ("DOWN1", "UP1", "DOWN2", "SCROLL")


class Pinch:
    """Hysteresis on a normalized fingertip gap."""

    def __init__(self):
        self.on = False

    def update(self, ratio):
        self.on = ratio < (PINCH_OFF if self.on else PINCH_ON)
        return self.on


def pose_of(extended, tuck):
    """extended: 4 bools index..pinky. tuck: thumb-to-ring-knuckle / hand size."""
    if not any(extended):
        return "thumb" if tuck > THUMB_OUT else "fist"  # thumbs-up = freeze
    if list(extended) == [1, 0, 0, 1] and tuck > THUMB_OUT:  # thumb out keeps pinches from matching
        return "rock"
    if list(extended) == [1, 0, 0, 0]:  # index only, thumb free: it clicks on the index base here
        return "fine"
    return "point"


class OneEuro:
    """One-Euro filter: heavy smoothing while still, little lag while moving fast. Pure."""

    def __init__(self):
        self.x = None

    def __call__(self, t, x):
        if self.x is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt, self.t = max(t - self.t, 1e-3), t
        alpha = lambda cutoff: 1 / (1 + 1 / (2 * math.pi * cutoff * dt))
        self.dx += alpha(EURO_D_CUTOFF) * ((x - self.x) / dt - self.dx)
        self.x += alpha(EURO_MIN_CUTOFF + EURO_BETA * abs(self.dx)) * (x - self.x)
        return self.x


def palm_shape(lm):
    """Knuckles relative to the wrist, scaled by hand size: hand angle, independent of fingers."""
    o = lm[WRIST]
    size = math.hypot(lm[MID_MCP].x - o.x, lm[MID_MCP].y - o.y) or 1e-6
    return [v for i in PALM for v in ((lm[i].x - o.x) / size, (lm[i].y - o.y) / size)]


def shape_dist(a, b):
    """Mean point distance between two palm shapes."""
    return sum(math.dist(a[i:i + 2], b[i:i + 2]) for i in range(0, len(a), 2)) / (len(a) // 2)


def edge_sides(pts):
    """pts: 21 (x, y) -> (top, bottom, left, right) warning 0/1: index tip past that edge of the box.
    Only the index counts: pointing low puts the wrist at the camera's bottom edge, that's fine."""
    u = (pts[INDEX][0] - BOX[0]) / (BOX[2] - BOX[0])
    v = (pts[INDEX][1] - BOX[1]) / (BOX[3] - BOX[1])
    return tuple(float(out) for out in (v < 0, v > 1, u < 0, u > 1))


def flat_curl(pts):
    """pts: 21 (x, y) -> (flat, curled). flat: palm tipped toward the camera's view so its length
    shrinks but its width doesn't. curled: fingertips pulled in toward the bottom of the palm."""
    d = lambda a, b: math.dist(pts[a], pts[b])
    width = d(5, 17) or 1e-6
    reach = sum(d(t, WRIST) for t in (8, 12, 16, 20)) / 4 / width
    return width / (d(WRIST, MID_MCP) or 1e-6) > FLAT_RATIO, reach < CURL_REACH


def box_from(points):
    """Index-tip samples -> axis-aligned rectangle (l, t, r, b), extremes trimmed."""
    xs, ys = sorted(p[0] for p in points), sorted(p[1] for p in points)
    k = int(len(xs) * CAL_TRIM)
    return tuple(round(v, 3) for v in (xs[k], ys[k], xs[-1 - k], ys[-1 - k]))


def thumb_up(pts):
    """pts: 21 (x, y). Thumb straight (no bend at its joints) and pointing up, within 45 degrees."""
    d = lambda a, b: math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
    straight = d(2, 4) / ((d(2, 3) + d(3, 4)) or 1e-6)
    dx, dy = pts[4][0] - pts[2][0], pts[2][1] - pts[4][1]  # dy > 0 = tip above its base (image y grows down)
    return straight >= THUMB_STRAIGHT and dy > abs(dx)


def ctrl_move(ext, thumb_out, thumb_straight_up):
    """Ctrl-mode pose -> 'neutral', 'ctrl+<letter>', 'enter' or None. Works at any hand angle
    (sideways Ctrl pose or upright under Ctrl lock): only finger up/down and where the thumb is."""
    if ext == [1, 0, 0, 1]:  # rock sign
        return "enter"
    if ext == [1, 0, 0, 0]:
        return "ctrl+v"
    if ext == [0, 0, 0, 1]:
        return "ctrl+z"
    if not any(ext):
        return "ctrl+d" if thumb_out and thumb_straight_up else None  # a fist's tucked thumb doesn't count
    if all(ext):
        return "neutral" if thumb_out else "ctrl+c"  # open hand / thumb tucked across the palm
    return None


def rock_ctrl(held, drift):
    """Ctrl-mode rock sign -> 'switch' (hand moved), 'enter' (held still ENTER_HOLD s) or None (still waiting)."""
    if drift > ROCK_STILL:
        return "switch"
    return "enter" if held >= ENTER_HOLD else None


def selftest():
    def run(seq):
        g, out = Gestures(), []
        for t, p in seq:
            out += g.update(t, p)
        return out

    assert run([(0, 1), (.1, 0), (.5, 0)]) == ["left_click"]
    assert run([(0, 1), (.1, 0), (.2, 1), (.3, 0), (.8, 0)]) == ["right_click"]
    assert run([(0, 1), (.3, 1), (.6, 0)]) == ["drag_start", "drag_end"]
    assert run([(0, 1), (.1, 0), (.2, 1), (.5, 1), (.7, 0)]) == ["scroll_start", "scroll_end"]
    assert run([(0, 0), (.5, 0)]) == []
    g = Gestures()
    g.update(0, 1), g.update(.3, 1)
    assert g.reset() == ["drag_end"] and g.state == "IDLE"
    p = Pinch()
    mid = (PINCH_ON + PINCH_OFF) / 2
    assert [p.update(r) for r in (mid, PINCH_ON - .01, mid, PINCH_OFF + .01, mid)] == [False, True, True, False, False]
    assert pose_of([0] * 4, THUMB_OUT - .1) == "fist"
    assert pose_of([0] * 4, THUMB_OUT + .1) == "thumb"
    assert pose_of([1, 0, 0, 1], THUMB_OUT + .1) == "rock"
    assert pose_of([1, 0, 0, 1], THUMB_OUT - .1) == "point"  # thumb folded = not rock
    assert pose_of([1] * 4, THUMB_OUT + .1) == "point"  # open palm stays a normal cursor
    assert pose_of([1, 0, 0, 0], .1) == "fine" and pose_of([1, 0, 0, 0], THUMB_OUT + .1) == "fine"
    e = OneEuro()
    held = [e(i / 30, .5 + (.01 if i % 2 else -.01)) for i in range(60)]
    assert max(held[30:]) - min(held[30:]) < .006   # +-0.01 jitter at rest shrinks a lot
    e = OneEuro()
    moved = [e(i / 30, i / 30) for i in range(31)]  # sweep at 1 box/s
    assert moved[-1] > .9                           # barely lags a real move
    up = [(0, 0)] * 2 + [(.5, .5), (.5, .4), (.5, .3)] + [(0, 0)] * 16    # thumb joints 2,3,4 in a vertical line
    bent = [(0, 0)] * 2 + [(.5, .5), (.5, .4), (.6, .4)] + [(0, 0)] * 16  # tip bent sideways at the last joint
    side = [(0, 0)] * 2 + [(.5, .5), (.6, .5), (.7, .5)] + [(0, 0)] * 16  # straight but pointing sideways
    assert thumb_up(up) and not thumb_up(bent) and not thumb_up(side)
    assert rock_ctrl(.5, .01) is None and rock_ctrl(2.1, .01) == "enter" and rock_ctrl(.5, .1) == "switch"
    assert ctrl_move([True, False, False, False], False, False) == "ctrl+v"
    assert ctrl_move([True, False, False, True], False, False) == "enter"
    assert ctrl_move([False, False, False, True], True, False) == "ctrl+z"
    assert ctrl_move([False] * 4, True, True) == "ctrl+d"
    assert ctrl_move([False] * 4, True, False) is None and ctrl_move([False] * 4, False, True) is None  # bent / fist
    assert ctrl_move([True] * 4, True, False) == "neutral" and ctrl_move([True] * 4, False, False) == "ctrl+c"
    assert ctrl_move([True, True, False, False], True, False) is None
    assert shape_dist([0, 0, 1, 1], [0, 0, 1, 1]) == 0 and abs(shape_dist([0, 0, 0, 0], [3, 4, 0, 0]) - 2.5) < 1e-9
    # wrist at 0; idx/mid/ring/pinky knuckles at y=-length across width .3; fingertips at y=-r
    hand = lambda r, length=.5: [(0, 0)] * 5 + [p for x in (-.15, 0, .05, .15) for p in ((x, -length), (0, 0), (0, 0), (x, -r))]
    mid = lambda u, v: [(.5, .5)] * 8 + [(BOX[0] + u * (BOX[2] - BOX[0]), BOX[1] + v * (BOX[3] - BOX[1]))] + [(.5, .5)] * 12
    assert edge_sides(mid(.5, .5)) == (0, 0, 0, 0)                     # centre: quiet
    assert edge_sides(mid(.5, 1.0)) == (0, 0, 0, 0) and edge_sides(mid(.99, .01)) == (0, 0, 0, 0)  # at the edge: quiet
    assert edge_sides(mid(.5, 1.2))[1] == 1 and edge_sides(mid(-.1, .5))[2] == 1  # outside: warn
    low = mid(.5, .95)
    low[0] = (.5, .999)                                                 # wrist at the camera's bottom edge
    assert edge_sides(low) == (0, 0, 0, 0)                              # index still inside: quiet
    assert flat_curl(hand(1.5)) == (False, False)              # upright open hand (width/length .6)
    assert flat_curl(hand(.25, length=.2)) == (True, True)     # tipped flat (short palm), tips pulled in
    assert flat_curl(hand(.6, length=.2)) == (True, False)     # tipped flat, fingers still out
    sweep = [(.5 + i / 1000 * .4, .1 + (i * 7 % 1001) / 1000 * .3) for i in range(1001)] + [(0, 0), (1, 1)]
    l, t, r, b = box_from(sweep)
    assert abs(l - .5) < .02 and abs(t - .1) < .02 and abs(r - .9) < .02 and abs(b - .4) < .02  # strays dropped
    print("self-test ok")


def draw(frame, lm, color):
    import cv2
    h, w = frame.shape[:2]
    px = [(int(p.x * w), int(p.y * h)) for p in lm]
    for a, b in BONES:
        cv2.line(frame, px[a], px[b], color, 2)
    for p in px:
        cv2.circle(frame, p, 3, (255, 255, 255), -1)
    return px


def extended(lm):
    d = lambda a, b: ((lm[a].x - lm[b].x) ** 2 + (lm[a].y - lm[b].y) ** 2) ** .5
    return [d(tip, WRIST) > d(pip, WRIST) for tip, pip in FINGERS]


def tracker(num_hands):
    """Yields (mirrored frame, landmarker result, monotonic time) until the camera fails."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(Path(__file__).with_name("hand_landmarker.task"))),
        running_mode=vision.RunningMode.VIDEO, num_hands=num_hands))
    cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    last_ts = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera read failed")
                return
            frame = cv2.flip(frame, 1)  # mirror so moving right moves the cursor right
            now = time.monotonic()
            last_ts = max(int(now * 1000), last_ts + 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            yield frame, landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), last_ts), now
    finally:
        cap.release()
        cv2.destroyAllWindows()


def calibrate():
    """Point with the index only and sweep the zone; open the hand to save. Returns the box or None."""
    import cv2
    global BOX
    pts, open_t, cams = [], None, tracker(1)
    for frame, res, now in cams:
        h, w = frame.shape[:2]
        if res.hand_landmarks:
            lm = res.hand_landmarks[0]
            ext = extended(lm)
            draw(frame, lm, (200, 200, 200))
            if ext == [True, False, False, False]:
                pts.append((lm[INDEX].x, lm[INDEX].y))
                open_t = None
            elif all(ext) and len(pts) > 30:
                open_t = open_t or now
                if now - open_t > POSE_HOLD:
                    break
            else:
                open_t = None
        for x, y in pts[-400:]:
            cv2.circle(frame, (int(x * w), int(y * h)), 2, (0, 200, 255), -1)
        if len(pts) > 30:
            l, t, r, b = box_from(pts)
            cv2.rectangle(frame, (int(l * w), int(t * h)), (int(r * w), int(b * h)), (0, 255, 0), 2)
        msg = "open hand to SAVE" if len(pts) > 30 else "point (index only) and sweep your zone"
        cv2.putText(frame, f"{msg}   {len(pts)} pts   Tab = redo   Esc = cancel", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 2)
        cv2.imshow("handmouse calibrate", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == 9:  # Tab: wipe the sweep and start over
            pts, open_t = [], None
            print("calibration reset")
        elif k == 27:
            print("calibration cancelled")
            cams.close()
            return None
    else:
        return None  # camera died
    cams.close()  # free the camera now, main() opens it again
    box = box_from(pts)
    if box[2] - box[0] < .1 or box[3] - box[1] < .1:
        print(f"zone too small {box}, run --calibrate again with a bigger sweep")
        return None
    BOX_FILE.write_text(json.dumps(box))
    BOX = box
    print(f"saved {box} to {BOX_FILE.name}")
    return box


def record_motions(takes, path):
    """Each take: 3 s countdown, 3 s of raw landmarks. Saves path. True when saved."""
    import cv2
    names, data, frames, i, start, cams = list(takes), {}, [], 0, None, tracker(1)
    for frame, res, now in cams:
        hand = res.hand_landmarks[0] if res.hand_landmarks else None
        if hand:
            draw(frame, hand, (200, 200, 200))
            start = start or now  # first countdown waits for your hand
        name = names[i]
        t = now - start if start else 0
        if not start:
            big, small = "?", "show your hand to start"
        elif t < 3:
            big, small = str(3 - int(t)), "next: " + takes[name]
        elif t < 6:
            if hand:
                frames.append([[round(p.x, 4), round(p.y, 4), round(p.z, 4)] for p in hand])
            big, small = f"REC {6 - t:.1f}", takes[name]
        else:
            data[name], frames, i, start = frames, [], i + 1, now
            print(f"{name}: {len(data[name])} frames")
            if i == len(names):
                break
            continue
        cv2.putText(frame, f"{i + 1}/{len(names)} {name}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        cv2.putText(frame, big, (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 255), 6)
        cv2.putText(frame, small + "   Esc = cancel", (20, 170), cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 255), 2)
        cv2.imshow("handmouse record", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            print("recording cancelled")
            cams.close()
            return False
    else:
        return False  # camera died
    cams.close()
    path.write_text(json.dumps(data))
    print(f"saved {path.name}")
    return True


def capture_pose(path, what, shape):
    """3 s countdown, then record shape(hand) for 5 s -> path. Returns True when saved."""
    import cv2
    shapes, start, cams = [], None, tracker(1)
    for frame, res, now in cams:
        hand = res.hand_landmarks[0] if res.hand_landmarks else None
        if hand:
            draw(frame, hand, (200, 200, 200))
            start = start or now  # countdown starts once your hand shows up
        t = now - start if start else 0
        if not start:
            big, small = "?", "show your hand to start"
        elif t < 3:
            big, small = str(3 - int(t)), "get into your " + what
        elif t < 8:
            if hand:
                shapes.append(shape(hand))
            big, small = f"HOLD {8 - t:.1f}", f"recording ({len(shapes)} frames)"
        else:
            break
        cv2.putText(frame, big, (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 255), 6)
        cv2.putText(frame, small + "   Esc = cancel", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
        cv2.imshow("handmouse pose capture", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            print("pose capture cancelled")
            cams.close()
            return False
    else:
        return False  # camera died
    cams.close()
    if len(shapes) < 30:
        print(f"hand seen in only {len(shapes)} frames, try again")
        return False
    mean = [sum(col) / len(shapes) for col in zip(*shapes)]
    devs = sorted(shape_dist(s, mean) for s in shapes)
    thr = round(max(CTRL_MIN_MATCH, 3 * devs[int(len(devs) * .95)]), 3)  # 3x your own wobble while holding
    path.write_text(json.dumps({"shape": [round(v, 4) for v in mean], "max": thr}))
    print(f"saved {what} to {path.name}: {len(shapes)} frames, wobble p95 {devs[int(len(devs) * .95)]:.3f}, "
          f"match threshold {thr}")
    return True


def main(control):
    import cv2
    from screeninfo import get_monitors

    user32 = ctypes.windll.user32
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, not scaled ones
    mons = sorted(get_monitors(), key=lambda m: not m.is_primary)
    mon_i = 0

    def mouse(flags, data=0):
        user32.mouse_event(flags, 0, 0, data, 0)  # ponytail: legacy API, SendInput if it ever misbehaves

    def key_down(n, up=False):
        user32.keybd_event(KEYS.get(n) or ord(n.upper()), 0, (n in ("left", "right")) | (2 * up), 0)  # 1 = extended (arrows), 2 = up

    def key(*names):
        """Press names in order, release in reverse (key('ctrl', 'alt', 'tab'))."""
        for n in names:
            key_down(n)
        for n in reversed(names):
            key_down(n, up=True)

    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def window_at(x, y):
        hwnd = user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)  # GA_ROOT
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        return hwnd if hwnd and name.value not in SHELL else None

    def minimize_at(x, y):
        if hwnd := window_at(x, y):
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE

    def close_at(x, y):
        if hwnd := window_at(x, y):
            user32.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE, same as clicking X: apps can still ask to save

    gest, mid_pinch, pinky_pinch = Gestures(), Pinch(), Pinch()
    cur = None          # smoothed cursor (x, y)
    scroll_y = scroll_acc = 0.0
    fps_t, fps = time.monotonic(), 0.0
    pose_raw, pose_t, fist_done, sw = "point", 0.0, False, False  # sw: Alt+Tab switcher open
    rock_uv, rock_drift = None, 0.0  # where the rock sign started, how far the index moved since
    hist = deque(maxlen=90)  # recent (t, x, y) cursor positions, for the fist/freeze lookback
    fx, fy, prev_uv, off = OneEuro(), OneEuro(), None, (0.0, 0.0)
    ctrl = json.loads(CTRL_FILE.read_text()) if CTRL_FILE.exists() else None
    if not ctrl:
        print("no ctrl.json yet: run --ctrl to teach the Ctrl pose")
    ctrl_max = max(ctrl["max"], CTRL_MIN_MATCH) * CTRL_LOOSEN if ctrl else 0
    flat, shut_t0, shut_done = False, None, False
    ctrl_mode, ctrl_seen, ctrl_score = False, -99.0, 9.0
    move_raw, move_t, move_done = None, 0.0, True
    ctrl_lock, lock_done = False, False  # Ctrl lock: Ctrl mode with any hand angle, toggled by a long thumbs-up
    from widget import Widget, GREY, BLUE, GREEN, AMBER, RED, PURPLE
    ui, last = Widget(), ""  # Buddy-style desktop panel: current mode + last action

    def say(msg):
        nonlocal last
        print(msg)
        last = msg

    def cursor_at(then):
        return next(((x, y) for t, x, y in hist if t >= then), cur)

    def fire(events):
        nonlocal scroll_acc
        for e in events:
            say(e.replace("_", " "))
            if not control:
                continue
            if e == "left_click": mouse(LEFTDOWN); mouse(LEFTUP)
            elif e == "right_click": mouse(RIGHTDOWN); mouse(RIGHTUP)
            elif e == "drag_start": mouse(LEFTDOWN)
            elif e == "drag_end": mouse(LEFTUP)
            elif e == "scroll_start": scroll_acc = 0.0

    cams = tracker(1)
    try:
        for frame, res, now in cams:
            h, w = frame.shape[:2]

            # active box: the part of the frame that maps onto the monitor
            cv2.rectangle(frame, (int(BOX[0] * w), int(BOX[1] * h)), (int(BOX[2] * w), int(BOX[3] * h)), (90, 90, 90), 1)

            sides = (0, 0, 0, 0)
            if res.hand_landmarks:
                lm = res.hand_landmarks[0]
                dist = lambda a, b: ((lm[a].x - lm[b].x) ** 2 + (lm[a].y - lm[b].y) ** 2) ** .5
                size = dist(WRIST, MID_MCP) or 1e-6
                r_mid, r_pinky = dist(THUMB, MIDDLE) / size, dist(THUMB, PINKY) / size
                r_base = dist(THUMB, INDEX_MCP) / size
                r_tuck = dist(THUMB, RING_MCP) / size
                pose = pose_of(extended(lm), r_tuck)
                if pose != pose_raw:
                    pose_raw, pose_t = pose, now
                    if pose == "thumb" and cur:  # undo the nudge from closing the hand
                        cur = cursor_at(now - FREEZE_LOOKBACK)
                    if sw and pose_raw != "rock" and cur:  # undo the nudge from leaving the rock sign
                        cur = cursor_at(now - SWITCH_LOOKBACK)
                stable = pose_raw if now - pose_t > POSE_HOLD else None

                # thumbs-up held LOCK_HOLD s: Ctrl lock on / off (once per hold)
                if pose_raw == "thumb" and now - pose_t >= LOCK_HOLD and not lock_done:
                    lock_done, ctrl_lock, move_done = True, not ctrl_lock, True
                    say("ctrl lock on" if ctrl_lock else "ctrl lock off")
                if pose_raw != "thumb":
                    lock_done = False

                # Ctrl mode: on the first frame the hand angle matches the pose captured by --ctrl (or locked)
                if ctrl or ctrl_lock:
                    ctrl_score = shape_dist(palm_shape(lm), ctrl["shape"]) if ctrl else 9.0
                    if ctrl_lock or ctrl_score < ctrl_max:
                        ctrl_seen = now
                        if not ctrl_mode:
                            ctrl_mode, move_done = True, True  # back to neutral first to arm moves
                            if cur:  # undo the drift from turning the hand
                                cur = cursor_at(now - CTRL_LOOKBACK)
                            fire(gest.reset())
                            say("ctrl mode on")

                # pinches and the cursor only count in the normal / fine pose
                # close: hand tipped flat freezes the cursor; flat + fingertips curled CLOSE_HOLD s -> close window
                was_flat = flat
                flat, curled = flat_curl([(p.x, p.y) for p in lm]) if not (ctrl_mode or sw) else (False, False)
                if flat and not was_flat and cur:  # undo the drift from tipping the hand
                    cur = cursor_at(now - CTRL_LOOKBACK)
                if flat and curled:
                    shut_t0 = shut_t0 or now
                    if now - shut_t0 > CLOSE_HOLD and not shut_done:
                        shut_done = True
                        say("close window")
                        if control:
                            close_at(*cur)
                else:
                    shut_t0, shut_done = None, False
                # monitor switch only with the index tip inside the box
                in_box = BOX[0] <= lm[INDEX].x <= BOX[2] and BOX[1] <= lm[INDEX].y <= BOX[3]

                pointing = pose_raw in ("point", "fine") and not sw and not ctrl_mode and not flat

                was = pinky_pinch.on
                if pinky_pinch.update(r_pinky) and not was and pointing and in_box:
                    mon_i, cur, off = (mon_i + 1) % len(mons), None, (0.0, 0.0)
                    say(f"monitor {mon_i + 1}/{len(mons)}")
                # click pinch: thumb to middle tip, or to the index base in slow (fine) mode
                click_to = INDEX_MCP if pose_raw == "fine" else MIDDLE
                pinched = mid_pinch.update(r_base if click_to == INDEX_MCP else r_mid)
                # curling into a fist passes through a pinch; drop it instead of clicking
                fire(gest.update(now, pinched) if pointing else gest.reset())

                # index tip -> 0..1 inside the box, smoothed. off = cursor offset from the hand:
                # fine mode grows it (cursor moves slower than the hand), normal movement shrinks it
                # back so the box mapping comes back without a jump.
                fu = fx(now, (lm[INDEX].x - BOX[0]) / (BOX[2] - BOX[0]))
                fv = fy(now, (lm[INDEX].y - BOX[1]) / (BOX[3] - BOX[1]))
                # in the switcher the rock sign's index steers the cursor over the window thumbnails
                live = (not gest.frozen and pointing) or (sw and pose_raw == "rock")
                if prev_uv and live:
                    du, dv = fu - prev_uv[0], fv - prev_uv[1]
                    if pose_raw == "fine":
                        off = (off[0] + du * (FINE_SPEED - 1), off[1] + dv * (FINE_SPEED - 1))
                    else:
                        k = max(0.0, 1 - CATCHUP * math.hypot(du, dv))
                        off = (off[0] * k, off[1] * k)
                prev_uv = (fu, fv)
                if pose_raw == "rock":  # Ctrl mode: still rock = Enter, moved rock = switcher
                    rock_uv = rock_uv or (fu, fv)
                    rock_drift = max(rock_drift, math.hypot(fu - rock_uv[0], fv - rock_uv[1]))
                else:
                    rock_uv, rock_drift = None, 0.0
                rock = rock_ctrl(now - pose_t, rock_drift) if pose_raw == "rock" else None
                u = min(max(fu + off[0], 0), 1)
                v = min(max(fv + off[1], 0), 1)
                m = mons[mon_i]
                target = (m.x + u * (m.width - 1), m.y + v * (m.height - 1))
                if cur is None or live:
                    cur = target
                hist.append((now, *cur))
                if pointing:  # only warn while the hand is steering the cursor
                    sides = edge_sides([(p.x, p.y) for p in lm])
                if control:
                    user32.SetCursorPos(int(cur[0]), int(cur[1]))

                # fist: minimize the window the cursor was on just before the curl dragged it away
                if stable == "fist" and not fist_done and not sw and not ctrl_mode and not flat:
                    fist_done = True
                    x, y = cursor_at(pose_t - FIST_LOOKBACK)
                    say("minimize")
                    if control:
                        minimize_at(x, y)
                if pose_raw != "fist":
                    fist_done = False

                # rock sign: open the switcher; point at a window, raise middle + ring to click it,
                # anything else cancels
                if sw:
                    if stable == "point" and all(extended(lm)):
                        say("switcher pick")
                        if control:
                            mouse(LEFTDOWN); mouse(LEFTUP)  # cursor already parked on the thumbnail
                        sw = False
                    elif stable and stable != "rock":
                        say("switcher esc")
                        if control:
                            key("esc")
                        sw = False
                elif stable == "rock" and not flat and (not ctrl_mode or rock == "switch"):
                    sw, move_done = True, True  # Ctrl mode: back to neutral before the next move
                    say("switcher open")
                    if control:
                        key("ctrl", "alt", "tab")  # sticky Alt+Tab, stays open without holding Alt

                ext = extended(lm)

                # Ctrl mode: pose (ctrl_move) -> Ctrl+letter once; back to neutral re-arms
                if ctrl_mode and not sw:  # switcher owns the hand while open
                    move = ctrl_move(ext, r_tuck > THUMB_OUT, thumb_up([(p.x, p.y) for p in lm]))
                    # locked: thumbs-up is also the unlock, so Ctrl+D waits for a short one to be released
                    hold_d = ctrl_lock and move_raw == "ctrl+d"
                    if move != move_raw:
                        if hold_d and THUMB_HOLD < now - move_t < LOCK_HOLD and not move_done:
                            move_done = True
                            say("ctrl+d")
                            if control:
                                key("ctrl", "d")
                        move_raw, move_t = move, now
                    if move_raw == "neutral":
                        move_done = False
                    elif move_raw == "enter":
                        if rock == "enter" and not move_done:
                            move_done = True
                            say("enter")
                            if control:
                                key("enter")
                    elif move_raw and now - move_t > THUMB_HOLD and not move_done and not (ctrl_lock and move_raw == "ctrl+d"):
                        move_done = True  # once per move
                        say(move_raw)
                        if control:
                            key("ctrl", move_raw[-1])

                if gest.state == "SCROLL":
                    scroll_acc += (lm[MID_MCP].y - scroll_y) * SCROLL_GAIN  # hand up = page moves up, like dragging it
                    ticks = int(scroll_acc / 40) * 40
                    if ticks and control:
                        mouse(WHEEL, ticks)
                    scroll_acc -= ticks
                scroll_y = lm[MID_MCP].y

                # --- what the tracker sees ---
                px = draw(frame, lm, (200, 200, 200))
                cv2.line(frame, px[THUMB], px[click_to], (0, 255, 0) if mid_pinch.on else (0, 200, 255), 2)
                cv2.line(frame, px[THUMB], px[PINKY], (255, 0, 255) if pinky_pinch.on else (120, 60, 120), 1)
                cv2.circle(frame, px[INDEX], 9, (0, 0, 255), 2)
                cv2.putText(frame, f"mid {r_mid:.2f}  base {r_base:.2f}  pinky {r_pinky:.2f}  tuck {r_tuck:.2f}  "
                            f"ctrl {ctrl_score:.2f}/{ctrl_max:.2f}  {'FLAT ' if flat else ''}"
                            f"{(move_raw or '?') if ctrl_mode else pose_raw}",
                            (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
            else:
                fire(gest.reset())
                if sw:  # ponytail: one dropped frame cancels the switcher; add a grace period if annoying
                    say("switcher esc (hand lost)")
                    if control:
                        key("esc")
                    sw = False
                cv2.putText(frame, "no hand", (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 255), 1)

            if ctrl_mode and now - ctrl_seen > CTRL_EXIT:
                ctrl_mode, fist_done = False, True  # a fist made while leaving Ctrl mode must not minimize
                say("ctrl mode off")

            fps = .9 * fps + .1 / max(now - fps_t, 1e-6)
            fps_t = now
            cv2.putText(frame, f"{'CTRL MODE' if ctrl_mode else 'SWITCH' if sw else gest.state}"
                        f"  mon {mon_i + 1}/{len(mons)}  {fps:.0f} fps"
                        + ("" if control else "  VIEW ONLY"), (10, 25), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
            m = mons[mon_i]
            ui.warn.update(sides, (m.x, m.y, m.width, m.height))
            gesture = {"DRAG": "Dragging", "SCROLL": "Scrolling"}.get(gest.state, "Clicking")
            ui.show(*("No hand", GREY) if not res.hand_landmarks else
                     ("Ctrl lock" + (f"  ·  unlock in {max(0, LOCK_HOLD - (now - pose_t)):.0f}s" if pose_raw == "thumb" else ""), AMBER)
                     if ctrl_lock else
                     ("Ctrl mode", AMBER) if ctrl_mode else
                     ("Switcher  ·  open hand picks", GREEN) if sw else
                     ("Flat  ·  curl to close", RED) if flat else
                     (f"Frozen  ·  Ctrl lock in {max(0, LOCK_HOLD - (now - pose_t)):.0f}s", GREY) if pose_raw == "thumb" else
                     (gesture, GREEN) if gest.state != "IDLE" else
                     ("Slow mode", PURPLE) if pose_raw == "fine" else
                     ("Pointing", BLUE), last)
            cv2.imshow("handmouse", frame)
            if cv2.waitKey(1) & 0xFF == 27:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if control and gest.state == "DRAG":
            mouse(LEFTUP)
        if control and sw:
            key("esc")
        cams.close()
        ui.close()


if __name__ == "__main__":
    if "--test" in sys.argv:
        selftest()
    elif "--calibrate" in sys.argv:
        if calibrate():
            main(control=True)
    elif "--ctrl" in sys.argv:
        if capture_pose(CTRL_FILE, "Ctrl pose", palm_shape):
            main(control=True)
    else:
        main(control="--view" not in sys.argv)
