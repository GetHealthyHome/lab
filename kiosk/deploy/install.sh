#!/bin/bash
# Lab Toolbox installer for a Raspberry Pi 3B+ running Raspberry Pi OS Lite (64-bit, Bookworm or newer).
#   sudo git clone https://github.com/GetHealthyHome/lab /opt/lab-toolbox
#   sudo /opt/lab-toolbox/kiosk/deploy/install.sh
# Run 1 carves a data partition off the SD card and reboots. Run 2 (the same command) installs
# everything and reboots into the kiosk screen. Running it again later is safe.
set -euo pipefail

ROOT_GB=8                       # system partition size; the rest of the card becomes /data
DEPLOY=$(cd "$(dirname "$0")" && pwd)
KIOSK=$(dirname "$DEPLOY")
BOOT=/boot/firmware
CMDLINE=$BOOT/cmdline.txt
say() { printf '\n== %s\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
[ -f "$CMDLINE" ] || { echo "$CMDLINE not found: this needs Raspberry Pi OS Bookworm or newer."; exit 1; }
[ "$KIOSK" = /opt/lab-toolbox/kiosk ] || { echo "Clone the repo to /opt/lab-toolbox first (the services expect it there)."; exit 1; }

# ---------- Run 1: the data partition ----------
if ! blkid -L LTDATA >/dev/null 2>&1; then
	if grep -q labtoolbox_split "$CMDLINE"; then
		echo "The data partition isn't there yet. What the startup step did last boot:"
		cat /run/initramfs/lab-toolbox-split.log 2>/dev/null || echo "(no notes: the startup step didn't run)"
		echo "Send a photo of the lines above, then press Enter to try again or Ctrl+C to stop."
		read -r _
	fi
	disk_gb=$(($(lsblk -bndo SIZE /dev/mmcblk0) / 1024 ** 3))
	[ "$disk_gb" -ge $((ROOT_GB + 6)) ] || { echo "The SD card is ${disk_gb} GB; use a 16 GB or bigger card."; exit 1; }
	say "Step 1 of 2: making room for a data partition"
	apt-get update
	apt-get install -y initramfs-tools e2fsprogs fdisk
	install -m755 "$DEPLOY/initramfs/lab-toolbox-hook" /etc/initramfs-tools/hooks/lab-toolbox
	install -m755 "$DEPLOY/initramfs/lab-toolbox-split" /etc/initramfs-tools/scripts/local-premount/lab-toolbox-split
	grep -q '^auto_initramfs=1' "$BOOT/config.txt" || echo 'auto_initramfs=1' >>"$BOOT/config.txt"
	# gzip, not trixie's default zstd: every kernel unpacks it and lsinitramfs reads it without extra tools
	echo COMPRESS=gzip >/etc/initramfs-tools/conf.d/lab-toolbox
	kv=$(uname -r)
	if [ -e "/boot/initrd.img-$kv" ]; then update-initramfs -u -k "$kv"; else update-initramfs -c -k "$kv"; fi
	# The firmware loads initramfs8 next to kernel8.img (Pi 3, 64-bit); newer Pi OS doesn't always put it there
	[ -e "$BOOT/kernel8.img" ] || { echo "$BOOT/kernel8.img not found; send the output of: ls $BOOT"; exit 1; }
	cp "/boot/initrd.img-$kv" "$BOOT/initramfs8"
	lsinitramfs "$BOOT/initramfs8" | grep lab-toolbox-split >/dev/null || # not -q: an early exit breaks the pipe and pipefail fails it
		{ echo "The startup file doesn't contain the partition step; not rebooting. Send this screen."; exit 1; }
	grep -q labtoolbox_split "$CMDLINE" || sed -i "1 s/\$/ labtoolbox_split=$ROOT_GB/" "$CMDLINE"
	say "Rebooting. The first boot takes a minute longer. When you can log in again, run this same command."
	sleep 5
	reboot
	exit 0
fi

say "Step 2 of 2: installing Lab Toolbox"
sed -i 's/ labtoolbox_split=[0-9]*//' "$CMDLINE"
rm -f /etc/initramfs-tools/hooks/lab-toolbox /etc/initramfs-tools/scripts/local-premount/lab-toolbox-split

# /data holds everything that changes: database, photos, settings file, Google key, logs, backups.
# The system partition can then be locked read-only (see the setup guide) and survive power cuts.
mkdir -p /data
grep -q 'LABEL=LTDATA' /etc/fstab || echo 'LABEL=LTDATA /data ext4 defaults,noatime,nofail 0 2' >>/etc/fstab
grep -q '/var/log/journal' /etc/fstab ||
	echo '/data/journal /var/log/journal none bind,nofail,x-systemd.requires-mounts-for=/data 0 0' >>/etc/fstab
systemctl daemon-reload
mountpoint -q /data || mount /data

say "Packages"
apt-get update
apt-get install -y git curl sqlite3 python3-venv python3-picamera2 fswebcam cage fonts-dejavu-core
apt-get install -y chromium || apt-get install -y chromium-browser
ln -sf "$(command -v chromium || command -v chromium-browser)" /usr/local/bin/lab-toolbox-browser

say "Kiosk user and data folders"
id kiosk >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin kiosk
usermod -aG video,gpio,render,input,i2c kiosk
mkdir -p /data/photos /data/backups /data/journal /var/log/journal
chown kiosk:kiosk /data /data/photos /data/backups
[ -f /data/lab-toolbox.env ] || install -m600 -o kiosk -g kiosk "$DEPLOY/lab-toolbox.env.example" /data/lab-toolbox.env
# An override logo can live on /data so it can be swapped while the system is locked; no file = the shipped lab-logo.png
ln -sfn /data/logo.png "$KIOSK/lab_toolbox/static/logo.png"

say "Python app"
cd "$KIOSK"
[ -d .venv ] || python3 -m venv --system-site-packages .venv # so the venv sees apt's picamera2
.venv/bin/pip install -e '.[pi]'

say "Services"
systemctl set-default multi-user.target # no desktop, if the card has one: the kiosk screen is the only display
install -m644 "$DEPLOY"/*.service "$DEPLOY"/*.timer /etc/systemd/system/
install -m644 "$DEPLOY/pam-lab-toolbox-screen" /etc/pam.d/lab-toolbox-screen
install -m644 "$DEPLOY/99-lab-toolbox-backlight.rules" /etc/udev/rules.d/99-lab-toolbox-backlight.rules
mkdir -p /etc/chromium/policies/managed
install -m644 "$DEPLOY/chromium-policy.json" /etc/chromium/policies/managed/lab-toolbox.json
systemctl daemon-reload
systemctl disable getty@tty1 # the screen service owns the display
systemctl enable lab-toolbox-kiosk lab-toolbox-sync lab-toolbox-cage lab-toolbox-screen lab-toolbox-backup.timer

say "Faster boot, fewer moving parts"
for s in bluetooth hciuart ModemManager triggerhappy cups apt-daily.timer apt-daily-upgrade.timer man-db.timer; do
	systemctl disable --now "$s" 2>/dev/null || true
done
grep -q '^dtoverlay=disable-bt' "$BOOT/config.txt" || echo 'dtoverlay=disable-bt' >>"$BOOT/config.txt"
grep -q '^disable_splash=1' "$BOOT/config.txt" || echo 'disable_splash=1' >>"$BOOT/config.txt"
raspi-config nonint do_i2c 0 # I2C on, for the NFC reader
# Swap in compressed RAM instead of on the SD card (newer Pi OS already does this)
if dpkg -s dphys-swapfile >/dev/null 2>&1; then
	systemctl disable --now dphys-swapfile || true
	apt-get install -y zram-tools
	printf 'ALGO=zstd\nPERCENT=50\n' >/etc/default/zramswap
fi
# Hardware watchdog: if the Pi ever freezes solid, it reboots itself within 15 s
mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\n' >/etc/systemd/system.conf.d/lab-toolbox.conf
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' >/etc/systemd/journald.conf.d/lab-toolbox.conf

say "Done. Next: put the Google key in /data/google-key.json and fill in /data/lab-toolbox.env (see the setup guide)."
say "Rebooting into the kiosk screen in 10 seconds (Ctrl+C to stay here)."
sleep 10
reboot
