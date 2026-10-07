"""Buddy-style status widget for the hand mouse: top-right of the main monitor, click-through.
Same look and window trick as Buddy's panel. Redraws only on change."""
import ctypes
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ON_TOP = False  # False = sits on the desktop behind windows, like Buddy; True = floats above (still click-through)

GREY, BLUE, GREEN, AMBER, RED, PURPLE = (120, 130, 160), (90, 150, 255), (80, 220, 140), (255, 180, 60), (255, 90, 90), (190, 120, 255)
CHEATS = [  # (gesture, what it does); None = divider
    ("Point", "move cursor"),
    ("Thumb + middle", "click · twice = right click"),
    ("", "hold = drag · tap+hold = scroll"),
    ("Index only", "slow · thumb on index base = click"),
    ("Thumb + pinky", "next monitor (in the box)"),
    ("Fist", "minimize window"),
    ("Rock sign", "switcher · open hand picks"),
    ("Flat + curl", "close window"),
    ("Thumbs-up", "freeze · hold 2 s = Ctrl lock"),
    None,
    ("Ctrl pose", "hand on its side"),
    ("   thumb across", "Ctrl+C"),
    ("   index up", "Ctrl+V"),
    ("   pinky up", "Ctrl+Z"),
    ("   thumb up", "Ctrl+D"),
    ("   rock still 2 s", "Enter"),
    ("   rock + move", "switcher"),
]

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
for f, args, res in [
    (gdi32.CreateCompatibleDC, [ctypes.c_void_p], ctypes.c_void_p),
    (gdi32.CreateDIBSection, [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                              ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD], ctypes.c_void_p),
    (gdi32.SelectObject, [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
    (gdi32.DeleteObject, [ctypes.c_void_p], wintypes.BOOL),
    (gdi32.DeleteDC, [ctypes.c_void_p], wintypes.BOOL),
    (user32.UpdateLayeredWindow, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
]:
    f.argtypes, f.restype = args, res


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]


BLEND = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha


class Strip:
    """Click-through window with per-pixel alpha (Buddy's Strip)."""

    def __init__(self, root, x, y, w, h, topmost=ON_TOP):
        self.w, self.h = w, h
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", topmost)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.withdraw()
        self.win.update_idletasks()
        self.hwnd = user32.GetParent(self.win.winfo_id())
        style = user32.GetWindowLongW(self.hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(self.hwnd, -20, style | 0x80000 | 0x20 | 0x80)  # LAYERED|TRANSPARENT|TOOLWINDOW
        self.dc = gdi32.CreateCompatibleDC(None)
        bits = ctypes.c_void_p()
        bmi = BITMAPINFOHEADER(biSize=40, biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
        self.bmp = gdi32.CreateDIBSection(self.dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        gdi32.SelectObject(self.dc, self.bmp)
        self.buf = np.ctypeslib.as_array((ctypes.c_uint8 * (w * h * 4)).from_address(bits.value)).reshape(h, w, 4)
        self.pos, self.size, self.origin = wintypes.POINT(x, y), wintypes.SIZE(w, h), wintypes.POINT(0, 0)

    def push(self, bgra):
        self.buf[:] = bgra
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(self.pos), ctypes.byref(self.size), self.dc,
                                   ctypes.byref(self.origin), 0, ctypes.byref(BLEND), 2)  # ULW_ALPHA

    def destroy(self):
        self.win.destroy()
        gdi32.DeleteDC(self.dc)
        gdi32.DeleteObject(self.bmp)


class EdgeWarn:
    """Buddy's border glow, in red, per side: lights the edge(s) of the active monitor your hand is
    close to, pulsing. No windows (and no work) while there's nothing to warn about."""
    DEPTH, FALLOFF = 70, 16      # px: strip size, how far the red reaches into the screen
    RED_BGR = np.array([60, 60, 255], np.float32)

    def __init__(self, root):
        self.root, self.strips, self.mon = root, [], None

    def update(self, sides, mon):
        """sides: (top, bottom, left, right) strength 0..1. mon: (x, y, w, h) of the active monitor."""
        if max(sides) <= 0:
            return self.hide()
        if mon != self.mon:
            self.build(mon)
        pulse = 0.65 + 0.35 * np.sin(2 * np.pi * time.time() * 2.5)  # soft 'bleep', 2.5 per second
        for s in self.strips:
            a = np.max([k * e for k, e in zip(sides, s.fade)], axis=0) * pulse
            img = np.empty(a.shape + (4,), np.uint8)
            img[..., :3], img[..., 3] = self.RED_BGR * a[..., None], a * 255  # premultiplied
            s.push(img.repeat(4, 0).repeat(4, 1)[:s.h, :s.w])

    def build(self, mon):
        self.hide()
        x, y, w, h = self.mon = mon
        T = self.DEPTH
        for sx, sy, sw, sh in [(0, 0, w, T), (0, h - T, w, T), (0, T, T, h - 2 * T), (w - T, T, T, h - 2 * T)]:
            s = Strip(self.root, x + sx, y + sy, sw, sh, topmost=True)
            ys, xs = np.mgrid[sy:sy + sh:4, sx:sx + sw:4].astype(np.float32) + 2  # quarter res: the glow is soft
            s.fade = [np.exp(-d / self.FALLOFF) for d in (ys, h - ys, xs, w - xs)]  # top, bottom, left, right
            s.win.deiconify()
            self.strips.append(s)

    def hide(self):
        for s in self.strips:
            s.destroy()
        self.strips, self.mon = [], None


BOLD = ImageFont.truetype("segoeuib.ttf", 15)
SMALL = ImageFont.truetype("segoeui.ttf", 13)


class Widget:
    """show(mode, colour, last action) each frame; it only redraws when one of them changes."""
    W = 330

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.win, self.key = None, None
        self.warn = EdgeWarn(self.root)

    def show(self, mode, color, last):
        if (mode, color, last) != self.key:
            self.key = (mode, color, last)
            self.render(mode, color, last)
        self.root.update()

    def render(self, mode, dot, last):
        clip = lambda s, n=44: s if len(s) <= n else s[:n - 1] + "…"
        rows = [(BOLD, "HAND MOUSE", (235, 235, 245)), (SMALL, mode, dot),
                (SMALL, "Last  " + clip(last or "-"), (170, 175, 190)), None]
        rows += [None if c is None else (SMALL, c[0], (200, 205, 220), clip(c[1], 32)) for c in CHEATS]
        g, pad, lh = 14, 16, 20  # glow margin, padding, line height
        w, h = self.W + 2 * g, sum(10 if r is None else lh for r in rows) + 2 * pad + 2 * g
        box = (g, g, w - g, h - g)
        halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(box, 16, fill=dot + (90,))  # faint glow in the status colour
        img = halo.filter(ImageFilter.GaussianBlur(8))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(box, 16, fill=(16, 17, 24, 215), outline=dot + (140,), width=1)
        y = g + pad
        for r in rows:
            if r is None:
                d.line((g + pad, y + 4, w - g - pad, y + 4), fill=(255, 255, 255, 30))
                y += 10
                continue
            font, text, color, *what = r
            x = g + pad
            if font is BOLD:
                d.ellipse((w - g - pad - 10, y + 5, w - g - pad, y + 15), fill=dot + (255,))
            d.text((x, y), text, font=font, fill=color + (255,))
            if what:  # second column: what the gesture does
                d.text((x + 115, y), what[0], font=font, fill=(130, 135, 155, 255))
            y += lh
        px = np.asarray(img, np.float32)
        bgra = np.concatenate([px[..., [2, 1, 0]] * px[..., 3:] / 255, px[..., 3:]], -1).astype(np.uint8)
        if not self.win or (self.win.w, self.win.h) != (w, h):
            if self.win:
                self.win.destroy()
            work = wintypes.RECT()
            user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA, primary monitor
            self.win = Strip(self.root, work.right - w - 8, work.top + 8, w, h)
            self.win.win.deiconify()
        self.win.push(bgra)
        if not ON_TOP:
            user32.SetWindowPos(self.win.hwnd, 1, 0, 0, 0, 0, 0x13)  # HWND_BOTTOM, NOSIZE|NOMOVE|NOACTIVATE

    def close(self):
        self.warn.hide()
        if self.win:
            self.win.destroy()
        self.root.destroy()
