#!/usr/bin/env python3
"""
delete-fingerprint-record.py — remove ONE specific record from the
Validity sensor's on-chip fingerprint database, by dbid.

This is deliberately narrow: it deletes exactly the dbid you name,
nothing else. Use dump-fingerprint-db.py first to find the dbid of
the record you actually want gone (e.g. a leftover Windows print).

This is DESTRUCTIVE and cannot be undone from Linux — the record is
erased from the sensor's flash. If the removed record belongs to a
Windows install you dual-boot into, fingerprint login in Windows will
stop working for that finger until re-enrolled there.

Usage:
  sudo python3 dump-fingerprint-db.py                     # find the dbid first
  sudo python3 delete-fingerprint-record.py <dbid> --yes   # then delete it
"""
import argparse
import os
import subprocess
import sys
import time

SERVICES = ["open-fprintd.service", "python3-validity.service"]


def stop_services():
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "mask", "--runtime", "python3-validity.service"], check=True)
    subprocess.run(["systemctl", "stop", *SERVICES], check=True)
    time.sleep(3)


def restore_services():
    subprocess.run(["systemctl", "unmask", "--runtime", "python3-validity.service"])
    subprocess.run(["systemctl", "start", *SERVICES])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dbid", type=int, help="record id to delete (from dump-fingerprint-db.py)")
    parser.add_argument("--yes", action="store_true", help="required, confirms you really mean it")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Run this with sudo: sudo python3 delete-fingerprint-record.py <dbid> --yes")

    if not args.yes:
        sys.exit(
            "Refusing to delete without --yes.\n"
            "Run dump-fingerprint-db.py first to confirm this dbid is the record\n"
            "you actually want gone, then re-run with --yes."
        )

    try:
        from validitysensor.init import open as open_sensor
        from validitysensor.db import db
    except ImportError:
        sys.exit(
            "validitysensor module not found — is python3-validity installed?\n"
            "  sudo apt-get install python3-validity"
        )

    print("Stopping fingerprint services so we can talk to the sensor directly…")
    stop_services()
    try:
        open_sensor()
        print(f"\nBEFORE deleting dbid {args.dbid}:")
        db.dump_all()

        record = db.get_user(args.dbid)
        if record is None:
            sys.exit(f"No user record found with dbid {args.dbid}. Nothing deleted.")
        print(f"\nAbout to delete: {record!r}")

        db.del_record(args.dbid)

        print(f"\nAFTER deleting dbid {args.dbid}:")
        db.dump_all()
    finally:
        print("\nRestoring fingerprint services…")
        restore_services()

    print(
        "\nDone. Run `fprintd-verify` (as your normal user, no sudo) to confirm "
        "your own fingerprint still matches."
    )


if __name__ == "__main__":
    main()
