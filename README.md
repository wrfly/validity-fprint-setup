# validity-fprint-setup

One-line installer + troubleshooting notes for getting **Validity Sensors**
fingerprint readers (`138a:0097` and related chip IDs) working on Ubuntu,
using the community [python-validity](https://github.com/uunicorn/python-validity)
/ [open-fprintd](https://launchpad.net/~uunicorn/+archive/ubuntu/open-fprintd)
driver — since these chips are **not** supported by the upstream `libfprint`
that ships with Ubuntu's stock `fprintd`.

This wraps the driver's own install steps into one script, and documents a
few failure modes that aren't covered in the upstream README, found while
setting this up on a real machine.

## Tested on

| | |
|---|---|
| Laptop | Lenovo ThinkPad X1 Carbon 5th Gen (model `20HR`) |
| Sensor | Validity Sensors `138a:0097`, reports itself as `VSI 55E FM209-001` |
| OS | Ubuntu 24.04 LTS (noble), kernel 6.8 |
| Display manager | GDM3, Wayland |

The driver's udev rules also match `138a:0090` and `06cb:009a`, and the PPA
publishes packages for Ubuntu 22.04 (jammy) too, so this should work more
broadly — just untested by me. If you try it on different hardware/OS,
please open an issue with the result.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/wrfly/validity-fprint-setup/main/install.sh | bash
```

The script only **installs and configures the driver and PAM**. It refuses
to run if it doesn't detect a supported sensor on the USB bus. It does not
touch your fingerprint data and does not enroll anything — that needs your
actual finger:

```bash
fprintd-enroll        # do NOT use sudo — see "Common mistakes" below
fprintd-verify         # should print "verify-match"
```

Once `fprintd-verify` succeeds, lock the screen (`Super+L`) and unlock with
your finger, or use it at the GDM login screen and at `sudo` prompts.

## What the installer actually does

1. Checks the OS release and that a known Validity sensor is present.
2. Adds `ppa:uunicorn/open-fprintd`.
3. Installs `open-fprintd`, `fprintd-clients`, `python3-validity`. This
   **removes** the stock `fprintd` / `libpam-fprintd` packages — they're
   incompatible (that's the whole reason this exists: stock `fprintd`
   reports "No devices available" for this chip).
4. `python3-validity`'s own postinst downloads Lenovo's Windows driver
   package and extracts the sensor firmware from it — no user action
   needed, but it does require network access to `download.lenovo.com`.
5. Installs two systemd drop-ins:
   - `python3-validity.service`: removes the driver's default `--debug`
     flag (otherwise every TLS-encrypted frame exchanged with the sensor
     gets dumped to the journal — harmless but very noisy), and adds
     `Restart=on-failure`.
   - `open-fprintd-resume.service`: restarts the driver after
     suspend/resume before running the stock resume hook, which on its
     own fails on this hardware — see "Fingerprint stops working after suspend" below.
6. Runs `pam-auth-update --enable fprintd`, which inserts `pam_fprintd.so`
   ahead of the password check in `/etc/pam.d/common-auth`. Fingerprint is
   tried first (10s timeout, 1 attempt) and falls back to your password
   automatically — it never locks you out.

Safe to re-run after an Ubuntu upgrade or if the packages get removed.

## Common mistakes

- **Never run `fprintd-enroll` or `fprintd-verify` with `sudo`.** The
  fingerprint gets tied to a Linux SID derived from the UID of whoever
  runs the enroll — `sudo fprintd-enroll` enrolls the print for **root**,
  not you, and `fprintd-verify` as yourself will then report
  `verify-no-match` even though the scan itself works fine.
- **Re-enrolling the same finger without deleting the old one first**
  fails with a driver exception containing the code `04c3`
  (upstream confirms this means "duplicate record" — see
  [python-validity#111](https://github.com/uunicorn/python-validity/issues/111)).
  Fix: `fprintd-delete "$USER"` before enrolling that finger again.

## Troubleshooting

### `verify-no-match` even though enroll succeeded and you're not using sudo

This sensor's flash retains records across boots/OS reinstalls. If this
machine used to dual-boot Windows (or you at some point ran
`sudo fprintd-enroll` by mistake), there can be a leftover record on the
sensor enrolled under a *different* identity. The driver's verify logic
matches your finger against every record on the sensor and only
afterwards checks whose it was — so your finger can "hit" someone else's
old record first and report no match for you.

Use the two helper scripts to inspect and fix this:

```bash
sudo python3 scripts/dump-fingerprint-db.py
```

This prints every record on the sensor, read-only. Your own Linux-enrolled
prints show up under a synthetic SID like
`S-1-5-21-111111111-1111111111-1111111111-<your uid>`. Anything with a
*different* `S-1-5-21-...` prefix is not yours — most likely a Windows
leftover. Note its `dbid` number, then:

```bash
sudo python3 scripts/delete-fingerprint-record.py <dbid> --yes
```

This deletes only that one record. **If this machine still dual-boots into
that Windows install, fingerprint login there will need to be re-enrolled**
— check for a Windows/NTFS partition (`lsblk -f`) before deleting if
you're not sure.

Both scripts handle the "resource busy" issue below automatically.

### Fingerprint stops working after suspend

Symptom: works after a fresh boot, but after closing the lid / suspending
the reader no longer responds until you reboot.
`systemctl --failed` shows `open-fprintd-resume.service` failed, and
`journalctl -u open-fprintd-resume` ends with
`usb.core.USBError: [Errno 19] No such device`.

Cause: on resume the sensor often drops off the USB bus and comes back with a
new device number (`journalctl -k | grep 'usb 1-9'` shows
`USB disconnect` then `new full-speed USB device`). The running driver
still holds the old handle, so the stock resume hook — which asks the
driver to reopen the sensor over D-Bus — fails. The driver's udev rule
should restart the service on remove/add, but both events arrive within
the same second, systemd merges the queued stop + start jobs into a no-op,
and the old process keeps running with a dead handle.

Fix: the installer drops in an override so the resume hook restarts the
driver first and only then runs the stock `resume.py`. Don't just replace
`resume.py` with the restart: open-fprintd marks the device "suspended"
when the machine sleeps and only clears that flag when `resume.py` calls
its `Resume` method. Without it, every verify request is silently queued
(`The service is suspended / offline, delay the call` in
`journalctl -u open-fprintd`) and the reader still looks dead.

If you installed with an older version of this script, just re-run it.
Or do it by hand:

```bash
sudo mkdir -p /etc/systemd/system/open-fprintd-resume.service.d
sudo tee /etc/systemd/system/open-fprintd-resume.service.d/override.conf <<'CONF'
[Service]
ExecStart=
ExecStartPre=/bin/udevadm settle --timeout=10
ExecStart=/bin/systemctl restart python3-validity.service
ExecStart=/usr/lib/open-fprintd/resume.py
CONF
sudo systemctl daemon-reload
sudo systemctl start open-fprintd-resume.service   # recover right now, no reboot
```

### `usb.core.USBError: [Errno 16] Resource busy` when touching the sensor directly

A udev rule (`/lib/udev/rules.d/60-python3-validity.rules`) auto-restarts
`python3-validity.service` on every USB add/change event for this sensor.
If you `systemctl stop python3-validity` and then try to open the device
yourself (e.g. via the driver's own `playground/factory-reset.py`), udev
can win the race and grab the device back out from under you.

Fix: mask the service first so udev's restart is a no-op, *then* stop it:

```bash
sudo systemctl daemon-reload
sudo systemctl mask --runtime python3-validity
sudo systemctl stop open-fprintd python3-validity
# ... do your direct USB access here ...
sudo systemctl unmask --runtime python3-validity
sudo systemctl start python3-validity open-fprintd
```

(`--runtime` makes the mask temporary — it's gone after a reboot even if
you forget to unmask.)

### Full factory reset instead

If you'd rather wipe the sensor's entire database and start over (this
removes **all** enrolled prints, Linux and Windows both):

```bash
sudo systemctl mask --runtime python3-validity
sudo systemctl stop open-fprintd python3-validity
sudo python3 /usr/share/python-validity/playground/factory-reset.py
sudo systemctl unmask --runtime python3-validity
sudo systemctl start python3-validity open-fprintd
fprintd-enroll
```

## Uninstall / revert

```bash
sudo apt-get remove open-fprintd fprintd-clients python3-validity
sudo apt-get install fprintd libpam-fprintd
sudo pam-auth-update --enable fprintd   # re-links PAM to the stock module
sudo rm -rf /etc/systemd/system/python3-validity.service.d
sudo rm -rf /etc/systemd/system/open-fprintd-resume.service.d
sudo add-apt-repository --remove ppa:uunicorn/open-fprintd
```

## Credits

All the actual driver work is [uunicorn/python-validity](https://github.com/uunicorn/python-validity)
and its `open-fprintd` D-Bus shim — this repo is just an installer and a
notes file on top of it. File sensor/protocol bugs there, not here; file
issues about this install script or the troubleshooting notes here.

## License

MIT — see [LICENSE](LICENSE). (Does not apply to python-validity /
open-fprintd themselves, which are separate upstream projects.)
