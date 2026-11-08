"""Cage spot checker (C3, C4). Like a parking-lot sensor: it only asks "is this space full or empty?"

Each spot is a box on a reference photo taken with the tool present. A spot that still looks like its
reference is full; one that looks very different is empty; anything in between is unclear.
"""
from PIL import Image
import numpy as np

# Mean grey-level difference (0-255) inside the box. Starting values: tune during Phase 1 calibration.
FULL_BELOW = 12.0
EMPTY_ABOVE = 30.0


def load_gray(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.int16)


def classify(reference: np.ndarray, current: np.ndarray, box: tuple[int, int, int, int],
             full_below: float = FULL_BELOW, empty_above: float = EMPTY_ABOVE) -> str:
    """box is (x, y, w, h) in reference-photo pixels. Returns "full", "empty" or "unclear"."""
    if reference.shape != current.shape:
        return "unclear"  # camera moved or resolution changed: recalibrate
    x, y, w, h = box
    diff = float(np.abs(reference[y:y + h, x:x + w] - current[y:y + h, x:x + w]).mean())
    if diff < full_below:
        return "full"
    if diff > empty_above:
        return "empty"
    return "unclear"


def decide(camera: str, checked_out: bool) -> str | None:
    """C4 table: what the camera sees vs. what the records say. Returns an alert type or None."""
    if camera == "unclear":
        return "check_camera"
    if camera == "empty" and not checked_out:
        return "missing_tool"
    if camera == "full" and checked_out:
        return "returned_without_scan"
    return None
