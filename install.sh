#!/usr/bin/env bash
#
# validity-fprint-setup — installer for Validity Sensors (138a:0097 and
# related) fingerprint readers on Ubuntu, using the community
# python-validity / open-fprintd driver.
#
# Repo:  https://github.com/wrfly/validity-fprint-setup
# Usage: curl -fsSL https://raw.githubusercontent.com/wrfly/validity-fprint-setup/main/install.sh | bash
#
# What this script does (all idempotent, safe to re-run e.g. after an
# Ubuntu upgrade):
#   1. Sanity-checks the OS and the presence of a Validity USB sensor.
#   2. Adds the ppa:uunicorn/open-fprintd PPA.
#   3. Installs open-fprintd, fprintd-clients, python3-validity
#      (this REPLACES the stock fprintd / libpam-fprintd packages,
#      which don't support this sensor at all).
#   4. Downloads+extracts the sensor firmware from Lenovo's Windows
#      driver package (done automatically by the python3-validity
#      postinst script on first install).
#   5. Disables the driver's default --debug logging (it dumps every
#      TLS frame to the journal otherwise).
#   6. Enables fingerprint auth in PAM (pam-auth-update --enable fprintd).
#
# What this script deliberately does NOT do:
#   - Enroll any fingerprints. That requires a human finger; run
#     `fprintd-enroll` yourself afterwards (see README).
#   - Touch the sensor's on-chip fingerprint database. If you dual-boot
#     Windows, there may be a leftover Windows-enrolled print in there
#     that causes verify-no-match; see scripts/dump-fingerprint-db.py
#     and scripts/delete-fingerprint-record.py for that, run manually.
#
# Tested on: Ubuntu 24.04 LTS (noble), ThinkPad X1 Carbon 5th (20HR),
#            Validity Sensors 138a:0097 ("VSI 55E FM209-001").
# Should also work on: Ubuntu 22.04 (jammy) and other Lenovo models
#            using Validity 138a:0090 / 138a:0097 / 06cb:009a sensors,
#            per the driver's udev rules — untested, please report back.

set -euo pipefail

log()  { printf '\033[1;34m[*]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] && die "Run this as your normal user, not root. It will call sudo itself when needed."
command -v sudo >/dev/null || die "sudo is required."
command -v apt-get >/dev/null || die "This script only supports Debian/Ubuntu (apt-get not found)."

# --- 1. OS + hardware sanity checks -----------------------------------
. /etc/os-release 2>/dev/null || true
CODENAME="${VERSION_CODENAME:-unknown}"
case "$CODENAME" in
  noble|jammy) : ;;
  *) warn "This was tested on Ubuntu 22.04 (jammy) / 24.04 (noble). Detected: ${PRETTY_NAME:-unknown} ($CODENAME). The PPA may not have packages for this release; continuing anyway." ;;
esac

if ! command -v lsusb >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq usbutils >/dev/null
fi

SENSOR_LINE="$(lsusb | grep -E '138a:009[07]|06cb:009a' || true)"
if [[ -z "$SENSOR_LINE" ]]; then
  warn "No known Validity sensor (138a:0090 / 138a:0097 / 06cb:009a) found on the USB bus."
  warn "This driver only supports those chips. Aborting so we don't install something useless."
  warn "Run 'lsusb' yourself to double check — if your reader shows a different vendor:product, this repo does not apply to it."
  exit 1
fi
log "Found sensor: $SENSOR_LINE"

# --- 2. Add PPA ---------------------------------------------------------
if ! grep -rq "uunicorn/open-fprintd" /etc/apt/sources.list.d/ 2>/dev/null; then
  log "Adding ppa:uunicorn/open-fprintd"
  sudo add-apt-repository -y ppa:uunicorn/open-fprintd
else
  log "PPA already present, skipping add-apt-repository."
fi

# --- 3. Install packages -------------------------------------------------
log "Installing open-fprintd, fprintd-clients, python3-validity (this will remove the stock fprintd/libpam-fprintd)…"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y open-fprintd fprintd-clients python3-validity

# --- 4. Firmware is fetched by the python3-validity postinst automatically.
#        Nothing to do here; just sanity check the service can start.

# --- 5. Quiet down the debug logging ------------------------------------
OVERRIDE_DIR=/etc/systemd/system/python3-validity.service.d
if [[ ! -f "$OVERRIDE_DIR/override.conf" ]]; then
  log "Disabling verbose --debug logging on python3-validity.service"
  sudo mkdir -p "$OVERRIDE_DIR"
  printf '[Service]\nExecStart=\nExecStart=/usr/lib/python-validity/dbus-service\n' | sudo tee "$OVERRIDE_DIR/override.conf" >/dev/null
  sudo systemctl daemon-reload
else
  log "Debug-logging override already present, skipping."
fi

log "Restarting driver services…"
sudo systemctl enable --now python3-validity.service open-fprintd.service
sudo systemctl restart python3-validity.service

sleep 3
if ! systemctl is-active --quiet python3-validity; then
  die "python3-validity.service failed to start. Check: sudo journalctl -u python3-validity -n 50"
fi

# --- 6. Enable fingerprint auth in PAM -----------------------------------
log "Enabling fingerprint authentication in PAM (GDM login, screen lock, sudo)…"
sudo pam-auth-update --enable fprintd

# --- Verify device is visible to fprintd ---------------------------------
log "Checking device visibility via fprintd-list…"
if command -v fprintd-list >/dev/null; then
  timeout 15 fprintd-list "$USER" || true
fi

cat <<'EOF'

============================================================
 Driver installed. Two manual steps remain (need your finger):

   1) Enroll a finger (run as your normal user, NOT sudo):
        fprintd-enroll

   2) Verify it worked:
        fprintd-verify

 If fprintd-verify says "verify-no-match" right after a successful
 enroll, and this machine dual-boots Windows, the sensor's flash may
 still hold a fingerprint enrolled from Windows — see the README's
 "Leftover Windows fingerprint" section and scripts/dump-fingerprint-db.py.

 If enroll fails with an error mentioning "04c3", you're trying to
 re-enroll a finger that's already registered for this user — run
 `fprintd-delete $USER` first, then enroll again.

 Once verify-match works: lock the screen (Super+L) and unlock with
 your finger, or use it at the GDM login screen / sudo prompts.
============================================================
EOF
