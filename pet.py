"""Gumball overlay for the hand mouse (the only on-screen overlay). Own process, launched by handmouse.py,
so nothing here can stall gesture tracking. Click-through, per-pixel alpha (widget.Strip).
Reacts to: games, media playing, hand-mouse gestures (sent on stdin as 'mood <name>' / 'event <name>'),
windows: stands on the foreground window or the taskbar, rides it when it's dragged, falls when it's
minimized/closed, gets squished when it's maximized, walks / jumps / long-jumps between windows and monitors,
sits after a while, sleeps after a long while.
He reacts calmly: after SETTLE s when you're calm, longer the more commands you chained in the last few
seconds, so a quick Ctrl+D, Enter, ... shows only where it ends (see settle() / busy()).
Eyes follow the cursor on every frame that has round open eyes. Sprites: sprites_v2/manifest.json.
  python pet.py [--parent PID] [--state <animation>]   (--state forces one, for testing)"""
import csv
import ctypes
import json
import math
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
from collections import deque
from ctypes import wintypes
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI: real pixels everywhere
import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402
from widget import Strip, user32  # noqa: E402

SPRITES = Path(r"C:\Users\misae\gumball\sprites_v2")
BUSY = Path(r"C:\Users\misae\gumball\claude_busy")  # pet_hook.py: one file per Claude Code session mid-turn
GAMES = {"rocketleague.exe", "brawlhalla.exe"}       # add any game's exe here
BROWSERS = ("chrome", "brave", "msedge", "firefox", "opera")  # media from these = a video (YouTube)
SIZE = 0.55        # sprite scale at 100 % DPI (256 px canvas -> ~140 px)
FOOT, HIP = 254, 200  # canvas rows: feet when standing, hips when sitting (legs hang below the ledge)
SIT_AFTER = 12     # s on the same perch, cursor still, before he sits
SLEEP_AFTER = 120  # ... before he falls asleep
REACH = 9          # px on the 256 canvas: how far the pupils move from centre (the whites are ~24 px radius)
GRAVITY = 2500     # px/s² at 100 % DPI, for falls
SHELL = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
MIC = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

kernel32, shcore, dwmapi = ctypes.windll.kernel32, ctypes.windll.shcore, ctypes.windll.dwmapi


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
for f, args, res in [
    (user32.GetForegroundWindow, [], wintypes.HWND),
    (user32.MonitorFromWindow, [wintypes.HWND, wintypes.DWORD], ctypes.c_void_p),
    (user32.MonitorFromPoint, [wintypes.POINT, wintypes.DWORD], ctypes.c_void_p),
    (user32.IsWindow, [wintypes.HWND], wintypes.BOOL),
    (user32.GetMonitorInfoW, [ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL),
    (user32.EnumDisplayMonitors, [ctypes.c_void_p, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM], wintypes.BOOL),
    (user32.EnumWindows, [WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
    (user32.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
    (user32.GetClassNameW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    (user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
    (user32.IsIconic, [wintypes.HWND], wintypes.BOOL),
    (user32.IsZoomed, [wintypes.HWND], wintypes.BOOL),
    (user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
    (user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
    (dwmapi.DwmGetWindowAttribute, [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long),
    (shcore.GetDpiForMonitor, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                               ctypes.POINTER(wintypes.UINT)], ctypes.c_long),
    (kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
    (kernel32.WaitForSingleObject, [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
]:
    f.argtypes, f.restype = args, res


def monitors():
    out = []
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(lambda h, *_: out.append(h) or True), 0)
    return out


def mon_info(hmon):
    """(work-area RECT, DPI scale) of a monitor."""
    mi, dx, dy = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO)), wintypes.UINT(96), wintypes.UINT(96)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))  # MDT_EFFECTIVE_DPI
    return mi.rcWork, dx.value / 96


def window_rect(hwnd):
    r = wintypes.RECT()  # DWM bounds = the visible frame, without the invisible resize border
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)):
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def perch(want, game_mon):
    """Where he should stand: (key, x, y, dpi). Pumped: another monitor than the game's. Otherwise he stays on
    the cursor's monitor: top edge of the foreground window if it's there (near its right corner), else
    that monitor's taskbar."""
    if want == "pumped" and game_mon:
        others = [m for m in monitors() if m != game_mon]
        if others:
            work, dpi = mon_info(others[0])
            return ("mon", others[0]), work.right - 90 * dpi, work.bottom, dpi
    fg = user32.GetForegroundWindow()  # can be NULL (mid Alt+Tab) or already gone
    hmon = monitor_at(*cursor())
    work, dpi = mon_info(hmon)
    if not user32.IsWindow(fg) or user32.MonitorFromWindow(fg, 2) != hmon:
        return ("mon", hmon), work.right - 90 * dpi, work.bottom, dpi
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(fg, name, 64)
    if fg and name.value not in SHELL and user32.IsWindowVisible(fg) and not user32.IsIconic(fg) \
            and not user32.IsZoomed(fg):
        r = window_rect(fg)
        if r.top - 160 * dpi > work.top and r.right - r.left > 250 * dpi:  # room above it for him
            return ("win", fg), r.right - 70 * dpi, r.top, dpi
    return ("mon", hmon), work.right - 90 * dpi, work.bottom, dpi


# ---------------------------------------------------------------- activity signals (background threads)
sig = {"claude": False, "dictate": False, "game": False, "game_mon": None, "media": None,
       "mood": "nohand", "mood_t": time.time(), "events": deque(), "activity": deque(maxlen=64)}


def mic_in_use():
    """Windows' privacy log: an app recording right now has LastUsedTimeStop == 0. Claude desktop or CLI only."""
    for sub in (MIC, MIC + r"\NonPackaged"):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                low = name.lower()
                if low.startswith("claude_") or low.endswith("#claude.exe"):
                    try:
                        with winreg.OpenKey(k, name) as a:
                            if winreg.QueryValueEx(a, "LastUsedTimeStop")[0] == 0 \
                                    and winreg.QueryValueEx(a, "LastUsedTimeStart")[0]:
                                return True
                    except OSError:
                        pass
    return False


def game_monitor(pids):
    """Monitor of the biggest visible window owned by a game process."""
    best = [0, None]

    def each(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            r = window_rect(hwnd)
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best[0]:
                best[:] = [area, hwnd]
        return True
    user32.EnumWindows(WNDENUMPROC(each), 0)
    return user32.MonitorFromWindow(best[1], 2) if best[1] else None


def signals():
    pids, t_games = set(), 0.0
    while True:
        now = time.time()
        busy = False
        for f in BUSY.glob("*") if BUSY.exists() else ():
            try:  # ponytail: 1 h stale guard covers a crashed session that never sent Stop
                busy |= now - f.stat().st_mtime < 3600
            except OSError:
                pass
        sig["claude"], sig["dictate"] = busy, mic_in_use()
        if now - t_games > 3:
            t_games = now
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
            pids = {int(r[1]) for r in csv.reader(out.splitlines()) if len(r) > 1 and r[0].lower() in GAMES}
        sig["game"], sig["game_mon"] = bool(pids), game_monitor(pids) if pids else None
        time.sleep(0.5)


def media():
    """media.ps1 prints the app ids of sessions playing right now, once a second."""
    import os
    p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(Path(__file__).with_name("media.ps1")), str(os.getpid())],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)
    for line in p.stdout:
        apps = line.strip().lower()
        sig["media"] = ("dance" if "applemusic" in apps else "watching" if any(b in apps for b in BROWSERS)
                        else "audio" if apps else None)


def hand():
    """Lines from handmouse.py on stdin: 'mood drag' (current state) or 'event left_click' (one-off).
    Whatever counts as "you just did something" (a new gesture, a Ctrl shortcut; not clicks, not pointing,
    not scroll up/down flips) is stamped into sig["activity"]: how busy you are decides how calm he stays."""
    family = lambda m: "scroll" if m.startswith("scroll") else m  # noqa: E731
    for line in sys.stdin or ():
        kind, _, name = line.strip().partition(" ")
        now = time.time()
        with LOG.open("a") as log:
            log.write(f"{now:.2f},{kind},{name}\n")
        if kind == "mood":
            if name in GESTURES and name != "edge" and family(name) != family(sig["mood"]):
                sig["activity"].append(now)
            sig["mood"], sig["mood_t"] = name, now
            print("gumball:", name, file=sys.stderr, flush=True)  # shows in the hand mouse console
        elif kind == "event":
            sig["events"].append(name)
            if name not in CLICKS:
                sig["activity"].append(now)


# ---------------------------------------------------------------- sprites
class Anim:
    """Frames + fps + loop flag + per-frame anchor row (the canvas row that sits on the ledge)."""

    def __init__(self, meta):
        self.frames = [Image.open(f).convert("RGBA") for f in sorted((SPRITES / meta["folder"]).glob("*.png"))]
        self.fps, self.loop = meta.get("fps", 20), meta.get("loop", True)
        bottoms = [(f.getchannel("A").getbbox() or (0, 0, 0, FOOT + 1))[3] - 1 for f in self.frames]
        if meta.get("anchor") != "hips":
            self.anchors = [FOOT] * len(self.frames)
        elif self.loop or max(bottoms) < 245:
            self.anchors = [HIP] * len(self.frames)  # seated the whole time
        else:  # sitting down / standing up: the ledge row slides from feet to hips as his feet come up
            self.anchors = [round(HIP + (FOOT - HIP) * min(1, max(0, (b - 234) / 18))) for b in bottoms]


MANIFEST = json.loads((SPRITES / "manifest.json").read_text())["animations"]
ANIMS = {name: Anim(meta) for name, meta in MANIFEST.items()}
ANIMS["idle"] = ANIMS.pop("idle_stand")


def squeezed(a):
    """A fake transition: the body squashed to a sliver mid-way (horizontal spin) instead of real motion."""
    widths = [(b[2] - b[0]) if (b := f.getchannel("A").getbbox()) else 0 for f in a.frames]
    return min(widths) < 0.45 * max(widths)


for _n in [n for n, a in ANIMS.items() if not a.loop and squeezed(a)]:
    del ANIMS[_n]  # ponytail: skipped (transitions -> crossfade, reactions -> none); real clips with these names drop back in
HUBS = ("idle", "idle_sit")
SITTERS = {n for n, a in ANIMS.items() if a.loop and a.anchors[0] == HIP and n not in HUBS}  # their _in starts seated
OUT = {"drag_hold": "drag_release",           # nicer way out than the generic _out
       "disgust": None}                       # dictation over: straight back, no reaction
FACES_RIGHT = {"long_jump_air", "jump_air", "ride_window"}  # travel sprites: mirrored when he heads left
SLIDE = 900        # px/s at 100 % DPI, sliding along the taskbar
FADE = 0.15        # s crossfade between poses that have no transition clip
# How calmly he reacts. Your detector treats ~0.3 s as the shortest deliberate hold (POSE_HOLD / THUMB_HOLD), so
# anything shorter is a pose passed through. Beyond that the wait grows with how much you chained lately.
SETTLE = 0.30      # s a gesture must stand before he reacts, when you're calm
SETTLE_STEP = 0.25  # s added for every further command in the last BUSY_WINDOW s
SETTLE_MAX = 1.5
BUSY_WINDOW = 4.0
EXIT_HOLD = 0.4    # s extra before he drops back to idle, so a hand blip doesn't bounce him
SCROLL_FLIP = 0.8  # s a scroll direction must last before his scroll animation follows it
SHOT_GAP = 1.2     # s minimum between two one-shot reactions (Ctrl+C, Enter...)
CLICK_QUIET, CLICK_GAP = 3.0, 8.0  # clicks never stop: he flinches only after 3 s of nothing else, once per 8 s
CLICKS = {"left_click", "right_click"}
LOG = Path(__file__).with_name("gestures.log")  # timestamped moods/events from the hand mouse: real timing data
if LOG.exists() and LOG.stat().st_size > 1_000_000:
    LOG.write_text("")
DIRECT = {("ctrl_lock_arming", "ctrl_locked")}  # arming ends in the locked pose: no detour through idle


def pick(*names):
    """First animation that exists: the wished-for one, then fallbacks."""
    return next((n for n in names if n in ANIMS), "idle")


GESTURES = {  # mood from handmouse -> loop
    "drag": pick("drag_hold"), "scroll_up": pick("scroll_up"), "scroll_down": pick("scroll_down"),
    "arming": pick("ctrl_lock_arming"), "freeze": pick("freeze"), "ctrl_lock": pick("ctrl_locked"),
    "ctrl": pick("ctrl_mode"), "slow": pick("slow_mode"), "switcher": pick("switcher_open"),
    "edge": pick("hand_near_edge"),
}
GESTURE_ANIMS = set(GESTURES.values())
CALM_ANIMS = {"idle", "idle_sit", "sit_taskbar", "sleep"}  # where he rests: reaching these = going back to idle
EVENTS = {  # one-off from handmouse -> one-shot clip
    "left_click": "click_react", "right_click": "right_click_react", "ctrl+c": "ctrl_copy", "ctrl+v": "ctrl_paste",
    "ctrl+z": "ctrl_undo", "ctrl+d": "ctrl_d", "enter": "enter_press", "minimize": "minimize_react",
    "close_window": "close_window_react"}
FIDGETS = [c for c in (["look_around_in", "look_around", "look_around_out"],) if all(n in ANIMS for n in c)]


def route(a, b):
    """Clips from loop a to loop b. Standing states hang off idle, seated ones off idle_sit:
    a_out back to a's hub, idle_sit_in / _out between hubs, then b_in."""
    clip = lambda n: [n] if n in ANIMS else []  # noqa: E731
    if a == b or (a, b) in DIRECT:
        return []
    if a not in HUBS:
        return clip(OUT.get(a, f"{a}_out")) + route("idle_sit" if a in SITTERS else "idle", b)
    h = b if b in HUBS else "idle_sit" if b in SITTERS else "idle"
    if a != h:
        return clip("idle_sit_in" if h == "idle_sit" else "idle_sit_out") + route(h, b)
    return clip(f"{b}_in")


def leg(pre, fly, post, b, dur, h=0, ease="line"):
    """One stretch of a trip: clips at the start, `fly` looping while he travels to b (None = the live perch)
    over dur s (arc height h; ease line / fall / arc), clips at the end."""
    pre, post = [c for c in pre if c in ANIMS], [c for c in post if c in ANIMS]
    secs = lambda cs: sum(len(ANIMS[c].frames) / ANIMS[c].fps for c in cs)  # noqa: E731
    return dict(pre=pre, fly=fly, post=post, b=b, dur=dur, h=h, ease=ease, t_pre=secs(pre))


# ---------------------------------------------------------------- eyes that follow the cursor
_eyejson = json.loads((SPRITES / "eyes.json").read_text())
EYE_AT = {n: [(e["x"], e["y"], e["radius"]) for e in v.values()] for n, v in _eyejson.get("states", {}).items()}
for n in ("idle", "look_around", "scroll_up", "scroll_down", "freeze", "no_hand", "head_bob", "click_react",
          "right_click_react", "watch_cursor_move", "yawn", "disgust"):  # drawn on the idle_stand face
    EYE_AT.setdefault(n, [(e["x"], e["y"], e["radius"]) for e in _eyejson["eyes"].values()])
_track, _cache = {}, {}


def locate(img, expect):
    """The drawn pupils of one frame: [(cx, cy, r, patch box, eye mask)] or None if they're not two round
    pupils sitting in white eyes (blink, squint, side-eye, pupil at the rim): then the frame keeps its face."""
    X0, Y0 = max(0, min(e[0] for e in expect) - 50), max(0, min(e[1] for e in expect) - 50)  # face area only
    a = np.asarray(img)[Y0:max(e[1] for e in expect) + 50, X0:max(e[0] for e in expect) + 50]
    expect = [(x - X0, y - Y0, r) for x, y, r in expect]
    dark = (a[..., :3].max(2) < 70) & (a[..., 3] > 200)
    white = (a[..., :3].min(2) > 215) & (a[..., 3] > 200)
    core = np.asarray(Image.fromarray(dark.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(7))) > 0
    ys, xs = np.nonzero(core)  # only thick dark blobs survive: pupils, not outlines
    found = []
    for i, (x, y, r) in enumerate(expect):
        ox, oy = expect[1 - i][:2]
        d_me, d_other = (xs - x) ** 2 + (ys - y) ** 2, (xs - ox) ** 2 + (ys - oy) ** 2
        sel = (d_me < 26 ** 2) & (d_me < d_other)
        if sel.sum() < 30:
            return None
        cx, cy = xs[sel].mean(), ys[sel].mean()
        r = math.sqrt(sel.sum() / math.pi) + 3  # measured: the filter shaved 3 px off the drawn pupil
        x0, y0 = max(0, int(cx) - 23), max(0, int(cy) - 23)
        x1, y1 = min(a.shape[1], int(cx) + 24), min(a.shape[0], int(cy) + 24)
        py, px = np.mgrid[y0:y1, x0:x1]
        dist = np.hypot(px - cx, py - cy)
        if dark[y0:y1, x0:x1][dist <= r - 1].mean() < 0.85 or white[y0:y1, x0:x1][(dist >= r + 2) & (dist <= r + 5)].mean() < 0.85:
            return None  # pupil not wholly inside a white eye (touching the rim, under a brow...)
        eye = (white[y0:y1, x0:x1] | (dist <= r + 3.5)) & (dist <= 22)  # where a pupil may be painted
        found.append((cx + X0, cy + Y0, r, (x0 + X0, y0 + Y0, x1 + X0, y1 + Y0), eye))
    return found


def eye_track(name):
    """Per animation (cached): located pupils per frame, or None when too few frames have them (then the
    whole animation keeps its drawn eyes, so pupils never pop between live and drawn)."""
    if name not in _track:
        res = None
        if name in EYE_AT and name in ANIMS:
            res = [locate(f, EYE_AT[name]) for f in ANIMS[name].frames]
            if sum(r is not None for r in res) < 0.8 * len(res):
                res = None
        _track[name] = res
    return _track[name]


def with_gaze(img, eyes, cursor, ox, oy, s):
    """Erase the drawn pupils and paint them again toward the cursor, clipped to the eye whites."""
    arr = np.array(img)
    for cx, cy, r, (x0, y0, x1, y1), eye in eyes:
        py, px = np.mgrid[y0:y1, x0:x1]
        arr[y0:y1, x0:x1][eye & (np.hypot(px - cx, py - cy) <= r + 3.5)] = (255, 255, 255, 255)
    for cx, cy, r, (x0, y0, x1, y1), eye in eyes:
        dx, dy = cursor[0] - (ox + cx * s), cursor[1] - (oy + cy * s)
        dist = math.hypot(dx, dy) or 1
        k = min(1.0, dist / (120 * s)) * REACH / dist  # cursor right on him -> pupils centre
        py, px = np.mgrid[y0:y1, x0:x1]
        arr[y0:y1, x0:x1][eye & (np.hypot(px - cx - dx * k, py - cy - dy * k) <= r)] = (0, 0, 0, 255)
    return Image.fromarray(arr)


def bgra(img, size, key=None):
    """Premultiplied BGRA at size x size; cached for plain sprite frames (key)."""
    if key and (key, size) in _cache:
        return _cache[key, size]
    a = np.asarray(img.resize((size, size), Image.LANCZOS), np.float32)
    out = np.concatenate([a[..., [2, 1, 0]] * a[..., 3:] / 255, a[..., 3:]], -1).astype(np.uint8)
    if key:
        _cache[key, size] = out
    return out


def cursor():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def monitor_at(x, y):
    return user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)


# ---------------------------------------------------------------- the pet
class Pet:
    def __init__(self, force=None, parent=None):
        self.root = tk.Tk()
        self.root.withdraw()
        self.force, self.parent = force, parent and kernel32.OpenProcess(0x100000, False, parent)  # SYNCHRONIZE
        now = time.time()
        self.win, self.pos, self.dpi, self.move, self.check_t = None, None, 1.0, None, 0.0
        self.loop, self.queue, self.dest, self.t0 = "idle", [], "idle", now  # playing: queued clips, then a loop
        self.want, self.want_t, self.perch_key, self.perch_t = "idle", 0.0, None, now
        self.moved_t, self.last_cur, self.fidget_t = now, None, now + 20
        self.rect, self.ride_t = None, 0.0
        self.pend, self.pend_t, self.event, self.shot_t = None, 0.0, None, 0.0  # event: (clip, "shot"|"click", t)
        self.sdir, self.sdir_cand, self.sdir_t = None, None, 0.0
        self.shown, self.last_img, self.fade_from, self.fade_t = None, None, None, 0.0

    def wanted(self, now, perch_kind):
        """Priority: game > the window under him moving > hand gesture > dictation > Claude working > media >
        hand just lost > idle (sits after SIT_AFTER, sleeps after SLEEP_AFTER)."""
        if self.force:
            return self.force
        m = sig["mood"]
        if sig["game"]:
            return pick("pumped")
        if now - self.ride_t < 0.4:
            return pick("ride_window")
        if m.startswith("scroll"):  # the hand strokes back and forth: follow a direction only once it lasts
            if m != self.sdir_cand:
                self.sdir_cand, self.sdir_t = m, now
            if not self.sdir or now - self.sdir_t > SCROLL_FLIP:
                self.sdir = self.sdir_cand
            m = self.sdir
        else:
            self.sdir = None
        if m in GESTURES:
            return GESTURES[m]
        if sig["media"] == "dance":
            return pick("dance_alt" if int(now / 16) % 2 else "dance", "head_bob")  # switches dance every 16 s
        if sig["media"]:
            return pick("watching_video" if sig["media"] == "watching" else "head_bob")
        if m == "nohand" and now - sig["mood_t"] < 5:
            return pick("no_hand")
        still = now - max(self.perch_t, self.moved_t)
        if still > SLEEP_AFTER:
            return pick("sleep")
        if still > SIT_AFTER:
            return pick("sit_taskbar" if perch_kind == "mon" else "idle_sit")
        return "idle"

    @staticmethod
    def busy(now):
        """(commands in the last BUSY_WINDOW s, seconds a gesture must stand before he follows it)."""
        n = sum(now - t < BUSY_WINDOW for t in list(sig["activity"]))
        return n, min(SETTLE_MAX, SETTLE + SETTLE_STEP * max(0, n - 1))

    def settle(self, now, want):
        """Seconds `want` must stand before he follows it. Gestures: calm = SETTLE, longer the more you chained
        (quick Ctrl+D, Enter, ... = he waits and shows only where it ends); back to idle = SETTLE + EXIT_HOLD.
        Window dragging is physics (near-instant); games, music, hand lost are slow signals (fixed)."""
        if want == pick("ride_window"):
            return 0.25
        if want == GESTURES["edge"]:
            return SETTLE
        if want in CALM_ANIMS:  # leaving a gesture: quick even when busy; a new gesture restarts the wait anyway
            return SETTLE + EXIT_HOLD
        if want in GESTURE_ANIMS:
            return self.busy(now)[1]
        return 0.5

    def start_move(self, now, old_key, new_key, x, y, dpi):
        """Plan the trip to the new perch as legs. To a taskbar: get down to the floor of the monitor he's on
        (fall if his window vanished, hop down otherwise, squished first if it got maximized), then slide along
        the taskbar, across monitors too. Onto a window: walk if short and level, else jump (long dive across
        monitors), or fall if his window vanished and the new one is lower."""
        x0, y0 = self.pos
        hw = old_key[1] if old_key[0] == "win" else None
        gone = hw and (not user32.IsWindow(hw) or user32.IsIconic(hw))
        maxed = hw and not gone and user32.IsZoomed(hw)
        up = ["stand_up"] if ANIMS[self.loop].anchors[0] == HIP and not self.queue else []
        fall_s = lambda h: min(1.2, max(0.3, math.sqrt(2 * max(h, 1) / (GRAVITY * dpi))))  # noqa: E731
        legs = []
        if new_key[0] == "mon":
            floor = mon_info(monitor_at(x0, y0 - 2))[0].bottom
            if y0 < floor - 4 * dpi:  # up on a window: straight down first
                if gone:
                    legs.append(leg([], "fall", ["fall_land"], (x0, floor), fall_s(floor - y0), ease="fall"))
                else:
                    legs.append(leg((["squished"] if maxed else up) + ["jump_takeoff"], "jump_air", ["jump_land"],
                                    (x0, floor), fall_s(floor - y0), h=40 * dpi, ease="fall"))
                up = []
            if abs(x - x0) > 4 * dpi:
                legs.append(leg(up, "ride_window", [], None, min(3.0, max(0.3, abs(x - x0) / (SLIDE * dpi)))))
        else:
            d = math.hypot(x - x0, y - y0)
            other_mon = monitor_at(x0, y0 - 2) != monitor_at(x, y - 2)
            if gone and y > y0:
                legs.append(leg([], "fall", ["fall_land"], None, fall_s(y - y0), ease="fall"))
            elif abs(y - y0) < 8 * dpi and d < 350 * dpi and not other_mon:
                legs.append(leg(up, "walk", [], None, max(0.3, d / (160 * dpi))))
            else:
                legs.append(leg((["squished"] if maxed else up) + ["jump_takeoff"], "long_jump_air" if other_mon else "jump_air",
                                ["jump_land"], None, min(1.1, max(0.45, d / 1500)), h=min(260, 70 + d / 4) * dpi, ease="arc"))
        self.move = legs or None

    def moving(self, now, x, y, dpi):
        """One frame of the current leg: (img, key, anchor, mirrored). Pre-clips where the leg starts, the trip,
        post-clips where it ends. The last leg ends on the live perch, so it follows a window moving meanwhile."""
        L = self.move[0]
        if "a" not in L:
            L["a"], L["t0"] = self.pos, now
        el = now - L["t0"]
        bx, by = L["b"] or (x, y)

        def play(clips, t):
            for c in clips:
                a = ANIMS[c]
                i = int(t * a.fps)
                if i < len(a.frames):
                    return a.frames[i], (c, i), a.anchors[i], False
                t -= len(a.frames) / a.fps
            return None

        if el < L["t_pre"]:
            self.pos = L["a"]
            return play(L["pre"], el)
        el -= L["t_pre"]
        if el < L["dur"]:
            t = el / L["dur"]
            e = t * t if L["ease"] == "fall" else t if L["ease"] == "line" else t * t * (3 - 2 * t)
            ax, ay = L["a"]
            self.pos = (ax + (bx - ax) * e, ay + (by - ay) * e - L["h"] * 4 * t * (1 - t))
            a = ANIMS[L["fly"]]
            i = int(el * a.fps) % len(a.frames)
            return a.frames[i], (L["fly"], i), a.anchors[i], L["fly"] in FACES_RIGHT and bx < ax
        self.pos = (bx, by)
        if len(self.move) == 1:
            self.dpi = dpi
        f = play(L["post"], el - L["dur"])
        if f:
            return f
        self.move.pop(0)
        if self.move:
            return self.moving(now, x, y, dpi)
        self.move = None  # arrived
        self.loop, self.queue, self.dest, self.t0 = "idle", [], "idle", now
        return self.frame(now, "idle") + (False,)

    def tick(self):
        now = time.time()
        if self.parent and now > self.check_t:  # hand mouse gone (even killed): go too
            self.check_t = now + 1
            if kernel32.WaitForSingleObject(self.parent, 0) == 0:
                return self.root.destroy()
        cur = cursor()
        if cur != self.last_cur:
            self.moved_t, self.last_cur = now, cur
        while sig["events"]:  # Ctrl+letters, Enter...: of a burst only the last one with a clip is kept
            e = sig["events"].popleft()
            clip = EVENTS.get(e) or ("monitor_switch" if e.startswith("monitor") else None)
            if clip in ANIMS and not (e in CLICKS and self.event and self.event[1] == "shot"):  # clicks never replace a shot
                self.event = (clip, "click" if e in CLICKS else "shot", now)
        if self.event:
            clip, kind, t = self.event
            last = max(sig["activity"], default=0.0)
            if now - t > 4:  # stale reaction: drop it
                self.event = None
            elif (now - last >= self.busy(now)[1] and now - self.shot_t >= SHOT_GAP if kind == "shot" else
                  now - last >= CLICK_QUIET and now - t >= SETTLE and now - self.shot_t >= CLICK_GAP) \
                    and not self.queue and not self.move and ANIMS[self.loop].anchors[0] == FOOT:
                self.queue, self.dest, self.t0, self.event, self.shot_t = [clip], self.loop, now, None, now  # one-shot, then back

        game_mon = sig["game_mon"] or (self.force == pick("pumped") and user32.MonitorFromWindow(user32.GetForegroundWindow(), 2))
        key, x, y, dpi = perch("pumped" if sig["game"] or game_mon else "", game_mon)
        if self.perch_key is not None and key != self.perch_key and not self.move:  # must hold 0.3 s (cursor on a seam)
            if key != self.pend:
                self.pend, self.pend_t = key, now
            if now - self.pend_t < 0.3:
                key, (x, y) = self.perch_key, self.pos  # stay put meanwhile
        if key[0] == "win":  # the window under him dragged along -> he rides it (a resize gets no reaction)
            r = window_rect(key[1])
            rect = (key, r.left, r.top, r.right - r.left, r.bottom - r.top)
            if self.rect and self.rect[0] == key:
                moved = max(abs(a - b) for a, b in zip(rect[1:3], self.rect[1:3])) >= 2
                resized = max(abs(a - b) for a, b in zip(rect[3:], self.rect[3:])) >= 8
                if moved and not resized:
                    self.ride_t = now
            self.rect = rect
        if self.pos is None:
            self.pos, self.dpi = (x, y), dpi
        if key != self.perch_key:
            if self.perch_key is not None and not self.move:
                self.start_move(now, self.perch_key, key, x, y, dpi)
            self.perch_key, self.perch_t = key, now
        want = self.wanted(now, key[0])
        if want != self.want:
            self.want, self.want_t = want, now
        stable = self.want if now - self.want_t > self.settle(now, want) else self.dest  # calm: no flicker

        flip = False
        if self.move:
            img, key_, anc, flip = self.moving(now, x, y, dpi)
        else:
            self.pos = (x, y)  # glued to the window: follows moves and resizes
            img, key_, anc = self.frame(now, stable)

        name = key_[0]
        s = SIZE * self.dpi
        size = round(256 * s)
        ox, oy = self.pos[0] - 128 * s, self.pos[1] - anc * s
        track = _track.get(key_[0])  # filled by the warm-up thread; never computed here, so no stalls
        if track and track[key_[1]]:  # live pupils (aimed at the mirrored cursor when the sprite gets mirrored)
            look = (2 * self.pos[0] - cur[0], cur[1]) if flip else cur
            img, key_ = with_gaze(img, track[key_[1]], look, ox, oy, s), None
        if flip:  # heading left: face the way he's going
            img, key_ = img.transpose(Image.FLIP_LEFT_RIGHT), key_ and key_ + ("flip",)
        if name != self.shown:  # pose change: crossfade from the last frame shown
            self.fade_from, self.fade_t, self.shown = self.last_img, now, name
        f = (now - self.fade_t) / FADE
        if self.fade_from is not None and f < 1:
            img, key_ = Image.blend(self.fade_from, img, f), None
        self.last_img = img
        if not self.win or self.win.w != size:
            if self.win:
                self.win.destroy()
            self.win = Strip(self.root, int(ox), int(oy), size, size, topmost=True)
            self.win.win.deiconify()
        self.win.pos.x, self.win.pos.y = int(ox), int(oy)
        self.win.push(bgra(img, size, key_))
        self.root.after(25, self.tick)

    def frame(self, now, want):
        """(image, cache key, anchor row): finish queued clips, route to want, then loop (one-shot loops,
        like the 2 s Ctrl-lock arming, hold their last frame)."""
        while self.queue:
            a = ANIMS[self.queue[0]]
            i = int((now - self.t0) * a.fps)
            if i < len(a.frames):
                return a.frames[i], (self.queue[0], i), a.anchors[i]
            self.queue.pop(0)
            self.t0 = now
            if not self.queue:
                self.loop = self.dest
        if want != self.loop:
            self.queue, self.dest, self.t0 = route(self.loop, want), want, now
            if self.queue:
                return self.frame(now, want)
            self.loop = want
            if want == GESTURES["arming"]:  # lasts exactly as long as the 2 s thumbs-up hold: clock starts with the hold
                self.t0 = max(sig["mood_t"], now - 1.5)
        if self.loop == want == "idle" and FIDGETS and now > self.fidget_t:  # now and then: look around, yawn
            self.fidget_t = now + random.uniform(20, 40)
            self.queue, self.dest, self.t0 = list(random.choice(FIDGETS)), "idle", now
            return self.frame(now, want)
        a = ANIMS[self.loop]
        i = int((now - self.t0) * a.fps)
        i = i % len(a.frames) if a.loop else min(i, len(a.frames) - 1)
        return a.frames[i], (self.loop, i), a.anchors[i]


if __name__ == "__main__":
    arg = lambda n: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else None  # noqa: E731
    for f in (signals, media, hand, lambda: [eye_track(n) for n in EYE_AT]):  # last one: warm the eye cache
        threading.Thread(target=f, daemon=True).start()
    pet = Pet(force=arg("--state"), parent=int(arg("--parent") or 0))
    pet.tick()
    pet.root.mainloop()
