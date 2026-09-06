"""Background watchdog for the voice denoiser.

This is launched by main.py as a fully DETACHED process -- meaning it is
NOT attached to the console window main.py runs in. That matters because
closing the console window (clicking the X) sends a close signal to every
process attached to that console and kills them almost instantly, giving
main.py very little real chance to clean up after itself.

This watchdog lives outside that console entirely, so it's unaffected by
the window closing. It just waits for main.py's process to actually exit
(for any reason at all), then restores the original microphone if main.py
didn't already do it cleanly on its own.
"""
import ctypes
import os
import subprocess
import sys
import time

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def is_process_alive(pid):
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return exit_code.value == STILL_ACTIVE


def restore_mic(switcher_path, mic_name):
    for role in ("2", "1", "0"):
        subprocess.run([switcher_path, "/SetDefault", mic_name, role], check=False)


def main():
    if len(sys.argv) != 4:
        return
    main_pid = int(sys.argv[1])
    switcher_path = sys.argv[2]
    mic_file = sys.argv[3]

    while is_process_alive(main_pid):
        time.sleep(0.5)

    # main.py has exited, one way or another. If it didn't get a chance to
    # clean up after itself (the marker file is still there), do it now.
    if os.path.exists(mic_file):
        try:
            with open(mic_file) as f:
                mic_name = f.read().strip()
            if mic_name:
                restore_mic(switcher_path, mic_name)
        except Exception:
            pass
        try:
            os.remove(mic_file)
        except Exception:
            pass


if __name__ == "__main__":
    main()
