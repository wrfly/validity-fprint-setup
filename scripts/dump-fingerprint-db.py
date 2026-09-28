#!/usr/bin/env python3
"""
dump-fingerprint-db.py — read-only dump of the Validity sensor's
on-chip fingerprint database.

Why this exists:
  fprintd only ever shows you fingers enrolled for *your own Linux user*.
  If this machine used to dual-boot Windows (or you accidentally enrolled
  a finger as root with `sudo fprintd-enroll`), there can be OTHER
  records sitting in the sensor's flash. Because the driver's verify
  logic matches your finger against *all* records on the sensor and
  only afterwards checks whose record it was, a leftover record can
  cause `fprintd-verify` to report "verify-no-match" even though your
  own enrollment succeeded.

  This script lists every user record and finger on the sensor so you
  can tell which dbid (if any) doesn't belong to you, before deciding
  whether to remove it with delete-fingerprint-record.py.

This script is READ-ONLY. It does not modify the sensor's database.

Usage:
  sudo python3 dump-fingerprint-db.py

Notes:
  - Must run as root (the sensor's flash requires raw USB access).
  - Temporarily masks+stops python3-validity.service and open-fprintd
    .service so the driver isn't holding the USB device (otherwise you
    get a "Resource busy" USBError — a udev rule auto-restarts the
    driver on USB re-plug/enumeration, so a plain `systemctl stop` can
    race with it). Both services are restored on exit, even on error.
  - Your own Linux-enrolled fingers show up under a synthetic SID of
    the form S-1-5-21-111111111-1111111111-1111111111-<your-uid>
    (that's how python-validity maps Linux UIDs when there's no real
    Windows SID mapping configured). Anything with a *different*
    S-1-5-21-... prefix is a real Windows-domain/local SID — almost
    certainly a leftover from a Windows install.
"""
import os
import pwd
import subprocess
import sys
import time

SERVICES = ["open-fprintd.service", "python3-validity.service"]


def sh(*args, check=True):
    return subprocess.run(args, check=check)


def stop_services():
    sh("systemctl", "daemon-reload")
    sh("systemctl", "mask", "--runtime", "python3-validity.service")
    sh("systemctl", "stop", *SERVICES)
    time.sleep(3)


def restore_services():
    subprocess.run(["systemctl", "unmask", "--runtime", "python3-validity.service"])
    subprocess.run(["systemctl", "start", *SERVICES])


def main():
    if os.geteuid() != 0:
        sys.exit("Run this with sudo: sudo python3 dump-fingerprint-db.py")

    try:
        from validitysensor.init import open as open_sensor
        from validitysensor.db import db
    except ImportError:
        sys.exit(
            "validitysensor module not found — is python3-validity installed?\n"
            "  sudo apt-get install python3-validity"
        )

    my_uid = os.getuid() if os.getuid() != 0 else None
    print("Stopping fingerprint services so we can talk to the sensor directly…")
    stop_services()
    try:
        open_sensor()
        info = db.db_info()
        print(f"\nSensor DB info: {info}\n")
        print("Records currently stored on the sensor:")
        print("-" * 60)
        db.dump_all()
        print("-" * 60)
        print(
            "\nYour Linux SID prefix looks like: "
            "S-1-5-21-111111111-1111111111-1111111111-<your uid>\n"
            f"(run `id -u` to see your own uid; you are '{pwd.getpwuid(os.getuid()).pw_name if my_uid else 'root'}')\n"
            "Any record with a DIFFERENT S-1-5-21-... prefix is not one of\n"
            "your Linux-enrolled fingers — likely a Windows leftover.\n"
            "\nTo remove such a record, note its dbid number from the dump\n"
            "above and run:\n"
            "  sudo python3 delete-fingerprint-record.py <dbid> --yes\n"
        )
    finally:
        print("\nRestoring fingerprint services…")
        restore_services()


if __name__ == "__main__":
    main()
