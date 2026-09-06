"""Diagnostic only: dumps every SoundVolumeView row whose Name or Device
Name contains a given search term, so you can see ALL the Command-Line
Friendly IDs SoundVolumeView has for that physical device -- including
both '\\Device\\' (real switchable endpoints) and '\\Subunit\\' (sub-nodes
of a device's topology, which /SetDefault can silently no-op against).

Usage:
    py -3.11 debug_devices.py realtek
    py -3.11 debug_devices.py "microphone array"
"""
import csv
import os
import shutil
import subprocess
import sys
import tempfile


def get_switcher_path():
    for name in ("svcl", "SoundVolumeView"):
        on_path = shutil.which(name)
        if on_path:
            return on_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for filename in ("svcl.exe", "SoundVolumeView.exe"):
        local_path = os.path.join(script_dir, filename)
        if os.path.exists(local_path):
            return local_path
    return None


def main():
    if len(sys.argv) != 2:
        print('Usage: py -3.11 debug_devices.py "search term"')
        return

    term = sys.argv[1].lower()
    switcher_path = get_switcher_path()
    if not switcher_path:
        print("Could not find SoundVolumeView/svcl.exe on PATH or next to this script.")
        return

    csv_path = os.path.join(tempfile.gettempdir(), "svv_devices_debug.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)

    subprocess.run([switcher_path, "/scomma", csv_path], check=False,
                    capture_output=True, text=True)

    if not os.path.exists(csv_path):
        print("SoundVolumeView didn't produce a CSV dump.")
        return

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    os.remove(csv_path)

    matches = [r for r in rows
               if term in (r.get("Name") or "").lower()
               or term in (r.get("Device Name") or "").lower()]

    if not matches:
        print(f"No rows matched '{term}'. Total rows in dump: {len(rows)}")
        return

    print(f"Found {len(matches)} matching row(s):\n")
    for r in matches:
        clfid = r.get("Command-Line Friendly ID", "")
        kind = "DEVICE" if "\\device\\" in clfid.lower() else ("SUBUNIT" if "\\subunit\\" in clfid.lower() else "OTHER")
        print(f"  [{kind:7}] Direction={r.get('Direction')!r}")
        print(f"            Name={r.get('Name')!r}")
        print(f"            Device Name={r.get('Device Name')!r}")
        print(f"            Command-Line Friendly ID={clfid!r}")
        print()


if __name__ == "__main__":
    main()
