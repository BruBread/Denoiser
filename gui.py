"""Small ON/OFF control panel for the voice denoiser.

This file only builds the window and button; all the actual audio/mic-
switching logic lives in main.py's Denoiser class. Run this instead of
main.py directly to get the GUI (run.bat has been updated to do this).
"""
import threading
import tkinter as tk

from main import Denoiser

WINDOW_W, WINDOW_H = 220, 140

ON_COLOR = "#2ecc71"
ON_COLOR_ACTIVE = "#27ae60"
OFF_COLOR = "#e74c3c"
OFF_COLOR_ACTIVE = "#c0392b"
BUSY_COLOR = "#f1c40f"

denoiser = Denoiser()
root = None
btn = None


def log(msg):
    print(msg)


def set_button_state(state):
    """state: 'on', 'off', or 'busy' (mid-switch, button disabled)."""
    if state == "on":
        btn.config(text="DENOISER: ON", bg=ON_COLOR, activebackground=ON_COLOR_ACTIVE, state="normal")
    elif state == "off":
        btn.config(text="DENOISER: OFF", bg=OFF_COLOR, activebackground=OFF_COLOR_ACTIVE, state="normal")
    else:
        btn.config(text="...", bg=BUSY_COLOR, activebackground=BUSY_COLOR, state="disabled")


def _do_turn_on():
    # Runs on a background thread -- start() does blocking subprocess calls
    # (~1s) to switch the default mic, which would otherwise freeze the GUI.
    denoiser.start(log=log)
    root.after(0, lambda: set_button_state("on"))


def _do_turn_off():
    denoiser.stop(log=log)
    root.after(0, lambda: set_button_state("off"))


def toggle():
    if denoiser.is_on:
        set_button_state("busy")
        threading.Thread(target=_do_turn_off, daemon=True).start()
    else:
        set_button_state("busy")
        threading.Thread(target=_do_turn_on, daemon=True).start()


def on_close():
    # Blocking here (rather than threading) is fine -- the window is going
    # away either way, and this guarantees the mic gets restored before exit.
    denoiser.shutdown()
    root.destroy()


def main():
    global root, btn

    print("Setting up (picking devices, loading model)...")
    denoiser.setup(log=log)
    print("Ready.")

    root = tk.Tk()
    root.title("Denoiser")

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - WINDOW_W) // 2
    y = (screen_h - WINDOW_H) // 3
    root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", on_close)

    btn = tk.Button(
        root, text="DENOISER: OFF", font=("Segoe UI", 14, "bold"),
        fg="white", bg=OFF_COLOR, activeforeground="white",
        activebackground=OFF_COLOR_ACTIVE, relief="flat", command=toggle,
    )
    btn.pack(expand=True, fill="both", padx=10, pady=10)

    # Pop to the very front once at launch, then release always-on-top so
    # it doesn't stay glued above other windows (like your call) all session.
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.after(400, lambda: root.attributes("-topmost", False))

    root.mainloop()


if __name__ == "__main__":
    main()