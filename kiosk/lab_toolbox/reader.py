"""Code reader (K1): turns gate-camera frames into ID and tool codes.

The camera sees the same label for many frames in a row, so `Debouncer` makes one hold-up count as one scan.
`Watcher` sets the pace: like a motion-sensor porch light, it only looks hard when something moves.
"""
import time
from typing import Callable

import numpy as np
from PIL import Image

# Starting values: tune on the Pi. MOTION is the mean grey-level change (0-255) between frames.
MOTION = 6.0
HOT_S = 5.0     # keep decoding every frame this long after motion or a code
IDLE_S = 0.2    # idle: ~5 motion checks a second and no decoding, which leaves CPU for the cage checker


def decode(image: Image.Image | np.ndarray) -> list[str]:
    """Return every QR / Code 128 value in the frame."""
    import zxingcpp  # imported here so the rest of the app runs on machines without it

    formats = (zxingcpp.BarcodeFormat.QRCode, zxingcpp.BarcodeFormat.Code128)
    return [r.text for r in zxingcpp.read_barcodes(image, formats=formats) if r.valid]


class Debouncer:
    """Pass a code through once, then ignore it until it has been out of view for `hold_s` seconds."""

    def __init__(self, hold_s: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.hold_s = hold_s
        self.clock = clock
        self.last_seen: dict[str, float] = {}

    def feed(self, codes: list[str]) -> list[str]:
        now = self.clock()
        fresh = [c for c in dict.fromkeys(codes) if now - self.last_seen.get(c, -1e9) > self.hold_s]
        for c in codes:
            self.last_seen[c] = now
        return fresh


class Watcher:
    """Turns camera frames into fresh codes. Idle frames only get a cheap motion check; motion or a
    readable code switches to decoding every frame for HOT_S seconds."""

    def __init__(self, decode: Callable = decode, clock: Callable[[], float] = time.monotonic):
        self.decode = decode
        self.clock = clock
        self.debouncer = Debouncer(clock=clock)
        self.prev: np.ndarray | None = None
        self.hot_until = 0.0

    def feed(self, frame: np.ndarray) -> list[str]:
        now = self.clock()
        small = frame[::8, ::8].astype(np.int16)
        if self.prev is not None and small.shape == self.prev.shape and np.abs(small - self.prev).mean() > MOTION:
            self.hot_until = now + HOT_S
        self.prev = small
        if not self.hot():
            return []
        codes = self.decode(frame)
        if codes:
            self.hot_until = now + HOT_S
        return self.debouncer.feed(codes)

    def hot(self) -> bool:
        return self.clock() < self.hot_until

    def pause(self) -> float:
        """Seconds to wait before the next frame. When hot, none: the camera's own frame rate sets the pace."""
        return 0.0 if self.hot() else IDLE_S
