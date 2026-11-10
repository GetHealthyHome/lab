"""Pi hardware: gate camera (picamera2), cage webcam (USB), buzzer (GPIO).

Each import happens inside the class, so the rest of the app and the tests run on any machine.
"""
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class PiGateCamera:
    """Camera module on the ribbon port. One small stream for reading codes, one big one for the session photo."""

    def __init__(self, photo_dir: str):
        from picamera2 import Picamera2

        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cam = Picamera2()
        # The Pi 3's ISP only offers YUV420 for the small stream; its first 480 rows are the grey (Y) image.
        self.cam.configure(self.cam.create_video_configuration(
            main={"size": (1280, 960)}, lores={"size": (640, 480), "format": "YUV420"}))
        if "AfMode" in self.cam.camera_controls:  # Camera Module 3: keep refocusing on labels held close
            from libcamera import controls

            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        self.cam.start()

    def frame(self) -> np.ndarray:
        """Next greyscale 640x480 frame; blocks until the camera delivers one."""
        with self.lock:
            return self.cam.capture_array("lores")[:480]

    def capture(self) -> str:
        path = self.photo_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jpg"
        with self.lock:
            self.cam.capture_file(str(path), name="main")
        return str(path)


class UsbCageCamera:
    """USB webcam(s) aimed at the shelves. Uses the fswebcam tool (apt install fswebcam).

    Two or more devices, separated by commas, give one picture with the views stacked top to bottom, like
    photos taped into one tall panorama. Everything downstream (spots, calibration, alerts) sees one camera.
    """

    def __init__(self, device: str = "/dev/video0", size: str = "1280x720"):
        self.devices = [d.strip() for d in device.split(",") if d.strip()]
        self.size = size

    def snapshot(self, path: str) -> str:
        if len(self.devices) == 1:
            return self._shot(self.devices[0], path)
        from PIL import Image

        # shortcut: one dead camera fails the whole picture (every spot reads "unclear"); a blank half would
        # read as "tool missing". Upgrade to per-camera spots if one camera proves flaky.
        parts = [f"{path}.{i}.jpg" for i in range(len(self.devices))]
        try:
            views = [Image.open(self._shot(d, p)).convert("RGB") for d, p in zip(self.devices, parts)]
            sheet = Image.new("RGB", (max(v.width for v in views), sum(v.height for v in views)))
            y = 0
            for v in views:
                sheet.paste(v, (0, y))
                y += v.height
            sheet.save(path, quality=90)
        finally:
            for p in parts:
                Path(p).unlink(missing_ok=True)
        return path

    def _shot(self, device: str, path: str) -> str:
        # -S 20 skips the first frames so auto exposure settles before the shot
        subprocess.run(["fswebcam", "-q", "-d", device, "-r", self.size, "--no-banner", "-S", "20", path],
                       check=True, timeout=30)
        if not Path(path).exists():  # fswebcam can exit 0 without writing when the camera is unplugged
            raise RuntimeError(f"no picture from {device}")
        return path


# (frequency Hz, seconds); 0 Hz is a gap. PRD feedback table.
SOUNDS = {
    "accepted": [(2000, 0.08)],                                   # short high beep
    "saved": [(1000, 0.12), (1300, 0.12), (1700, 0.2)],           # rising three-note chime
    "rejected": [(250, 0.15), (0, 0.08), (250, 0.15)],            # low double buzz
    "wrong_battery": [(900, 0.2), (600, 0.3)],                     # falling two-tone
    "locked": [(700, 0.12), (1000, 0.12)] * 4,                     # warble alarm
}


class Buzzer:
    """Passive piezo or small amp on a GPIO pin. With no pin it stays silent (bench testing)."""

    def __init__(self, pin: int | None = None):
        self.device = None
        self.lock = threading.Lock()
        if pin is not None:
            from gpiozero import PWMOutputDevice

            self.device = PWMOutputDevice(pin)

    def play(self, sound: str) -> None:
        """Returns at once; the tune plays on its own thread so a scan never waits for it."""
        if self.device:
            threading.Thread(target=self._play, args=(SOUNDS[sound],), daemon=True).start()

    def _play(self, notes) -> None:
        with self.lock:
            for freq, secs in notes:
                if freq:
                    self.device.frequency = freq
                    self.device.value = 0.5
                time.sleep(secs)
                self.device.value = 0
