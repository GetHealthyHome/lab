"""Sync service: its own process (systemd), so a slow network never touches the scan screen.

Every PUSH_SECONDS it drains the outbox to Google; every PULL_SECONDS it copies tools, spots and people down.
Run: KIOSK_DB=kiosk.db GOOGLE_KEY_FILE=/data/key.json SHEET_ID=... DRIVE_FOLDER_ID=... python -m lab_toolbox.sync_service
"""
import logging
import os
import time

from . import db, sync
from .google import GoogleSink

log = logging.getLogger("lab_toolbox.sync")

PUSH_SECONDS = 30   # PRD: the queue syncs within 5 minutes of reconnecting
PULL_SECONDS = 600


def run_once(conn, sink, do_pull: bool, kiosk_id: str = "gate-1") -> bool:
    """One round. Returns True if it pulled."""
    while (n := sync.sync_once(conn, sink)) == 100:  # full batch: keep going until the backlog is gone
        log.info("sent %d", n)
    sink.report_health(kiosk_id)
    if not do_pull:
        return False
    if sync.pending(conn):  # a change of ours not in the Sheet yet would be overwritten by the Sheet's old copy
        log.info("pull waits: %d item(s) still to send", sync.pending(conn))
        return False
    log.info("pulled %s", sink.pull())
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect(os.environ.get("KIOSK_DB", "kiosk.db"))
    sink = GoogleSink.from_service_account(
        os.environ["GOOGLE_KEY_FILE"], conn, os.environ["SHEET_ID"], os.environ["DRIVE_FOLDER_ID"]
    )
    last_pull = 0.0
    while True:
        do_pull = time.monotonic() - last_pull >= PULL_SECONDS
        try:
            if run_once(conn, sink, do_pull, os.environ.get("KIOSK_ID", "gate-1")):
                last_pull = time.monotonic()
        except Exception:  # offline or Google hiccup: the outbox keeps everything, try again next round
            log.exception("sync round failed (%d still queued)", sync.pending(conn))
        time.sleep(PUSH_SECONDS)


if __name__ == "__main__":
    main()
