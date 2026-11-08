"""Code reader (K1): turns gate-camera frames into ID and tool codes.

The camera sees the same label for many frames in a row, so `Debouncer` makes one hold-up count as one scan.
"""
import time
from typing import Callable

from PIL import Image


def decode(image: Image.Image) -> list[str]:
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
