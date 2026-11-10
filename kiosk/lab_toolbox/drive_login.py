"""One-time sign-in so photos go into a person's own Drive.

Needed when there is no Shared Drive: the service account that writes the Sheet has no Drive storage, so its
photo uploads are refused. This asks Google for a code to type in on a phone or computer (the Pi needs no
browser), makes the photo folder, and leaves the sign-in where the sync service picks it up.

Run on the Pi: sudo -u kiosk /opt/lab-toolbox/kiosk/.venv/bin/python -m lab_toolbox.drive_login
"""
import json
import os
import time

from .google import DRIVE_FILE_SCOPE

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
FOLDER_NAME = "Lab Toolbox photos"


def login(client: dict, http, sleep=time.sleep, say=print) -> dict:
    """Returns what the sync service needs: the sign-in and the folder it may write to."""
    c = client.get("installed") or client  # the Cloud console's download wraps it in "installed"
    r = http.post(DEVICE_URL, data={"client_id": c["client_id"], "scope": DRIVE_FILE_SCOPE})
    if r.status_code != 200:
        raise SystemExit(f"Google refused the sign-in request: {r.text[:300]}")
    d = r.json()
    say(f"\nOn a phone or computer, open  {d['verification_url']}\nand type this code:  {d['user_code']}\n\nWaiting for you to approve...")
    wait, deadline = d.get("interval", 5), time.monotonic() + d["expires_in"]
    while True:
        sleep(wait)
        r = http.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                       "device_code": d["device_code"],
                                       "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        body = r.json()
        if r.status_code == 200:
            break
        if body.get("error") == "slow_down":
            wait += 5
        elif body.get("error") != "authorization_pending":
            raise SystemExit(f"Google did not approve the sign-in: {body.get('error')}")
        if time.monotonic() > deadline:
            raise SystemExit("The code ran out before it was approved. Run the command again.")
    # shortcut: signing in again makes a second folder, since this app can only see folders it made; merge by hand.
    r = http.post(FILES_URL, headers={"Authorization": f"Bearer {body['access_token']}"},
                  json={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"})
    if r.status_code != 200:
        raise SystemExit(f"Signed in, but Google refused to make the photo folder: {r.text[:300]}")
    return {"type": "authorized_user", "client_id": c["client_id"], "client_secret": c["client_secret"],
            "refresh_token": body["refresh_token"], "folder_id": r.json()["id"]}


def main() -> None:
    import requests

    with open(os.environ.get("GOOGLE_CLIENT_FILE", "/data/google-client.json")) as f:
        client = json.load(f)
    info = login(client, requests)
    out = os.environ.get("GOOGLE_USER_FILE", "/data/google-user.json")
    with open(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:  # it is a password: owner only
        json.dump(info, f)
    print(f'\nSigned in. Photos will go to the folder "{FOLDER_NAME}" in that account\'s Drive.'
          "\nNow run:  sudo systemctl restart lab-toolbox-sync")


if __name__ == "__main__":
    main()
