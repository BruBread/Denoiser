import csv
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winsound

import numpy as np
import sounddevice as sd
import torch

import df.logger
df.logger.get_commit_hash = lambda: "unknown"
df.logger.get_branch_name = lambda: "unknown"

from df import init_df, enhance

SAMPLE_RATE = 48000
BLOCK_SIZE = 1920
BUDGET_MS = (BLOCK_SIZE / SAMPLE_RATE) * 1000  # time available per chunk to stay real-time

torch.set_num_threads(2)  # benchmarked: fewer threads = lower latency and CPU
                          # usage for chunks this small; os.cpu_count() thrashes


def find_device(name_contains, kind):
    devices = sd.query_devices()
    matches = []
    for i, d in enumerate(devices):
        if name_contains.lower() in d["name"].lower():
            if kind == "input" and d["max_input_channels"] > 0:
                matches.append((i, d["name"]))
            elif kind == "output" and d["max_output_channels"] > 0:
                matches.append((i, d["name"]))
    return matches


def find_device_wasapi(name_contains, kind):
    """Same as find_device, but restricted to the WASAPI host API.
    PortAudio's default (MME) host API truncates device names to 31
    characters, which breaks exact-name matching against SoundVolumeView.
    WASAPI reports full, untruncated names that match what Windows/
    SoundVolumeView actually expect."""
    try:
        hostapis = sd.query_hostapis()
        wasapi_index = next(i for i, h in enumerate(hostapis) if "wasapi" in h["name"].lower())
    except StopIteration:
        return []
    devices = sd.query_devices()
    matches = []
    for i, d in enumerate(devices):
        if d["hostapi"] != wasapi_index:
            continue
        if name_contains.lower() in d["name"].lower():
            if kind == "input" and d["max_input_channels"] > 0:
                matches.append((i, d["name"]))
            elif kind == "output" and d["max_output_channels"] > 0:
                matches.append((i, d["name"]))
    return matches


def choose_microphone():
    devices = sd.query_devices()
    print("\nAvailable microphones:")
    input_devices = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and "cable" not in d["name"].lower():
            input_devices.append((i, d["name"]))
            print(f"  [{len(input_devices) - 1}] {d['name']}")

    while True:
        choice = input("\nPick your microphone by number: ").strip()
        if choice.isdigit() and 0 <= int(choice) < len(input_devices):
            return input_devices[int(choice)][0]
        print("Invalid choice, try again.")


def get_switcher_path():
    """Find SoundVolumeView (or its console edition svcl.exe), either on PATH
    (installed via install.ps1/Chocolatey) or sitting next to this script.
    NirCmd's device-switching command is unreliable for recording devices on
    Windows 10/11, so we use SoundVolumeView instead."""
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


def query_switcher_devices(switcher_path):
    """Dump SoundVolumeView's own device list via /scomma and parse it.

    We learned the hard way that SoundVolumeView's 'Name' column (e.g.
    'CABLE Output') does NOT match the full WASAPI name Python sees
    (e.g. 'CABLE Output (VB-Audio Virtual Cable)'). Feeding /SetDefault
    the WASAPI name matches nothing -- and SoundVolumeView treats a
    no-match as a silent no-op that still exits 0, which is exactly why
    every previous attempt looked like it worked but did nothing.

    Rather than guess at a name-translation rule, we ask SoundVolumeView
    directly for its 'Command-Line Friendly ID' column, which is the
    exact deterministic identifier /SetDefault actually wants (e.g.
    'VB-Audio Virtual Cable\\Device\\CABLE Output\\Capture')."""
    csv_path = os.path.join(tempfile.gettempdir(), "svv_devices.csv")
    try:
        if os.path.exists(csv_path):
            os.remove(csv_path)
    except Exception:
        pass
    try:
        subprocess.run([switcher_path, "/scomma", csv_path], check=False,
                        capture_output=True, text=True)
        if not os.path.exists(csv_path):
            return []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return rows
    except Exception:
        return []
    finally:
        try:
            os.remove(csv_path)
        except Exception:
            pass


def _normalize(name):
    """Lowercase and strip everything but letters/digits, so 'CABLE Output
    (VB-Audio Virtual Cable)' and 'CABLE Output' compare equal on their
    shared core, regardless of parens/spacing/driver-suffix differences
    between what WASAPI reports and what SoundVolumeView calls things."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _is_device_id(row):
    """True if this row's Command-Line Friendly ID is a real switchable
    endpoint ('...\\Device\\...'), as opposed to a '...\\Subunit\\...' ID.
    Some audio chips (notably certain Realtek laptop drivers) expose their
    inputs as sub-nodes of one physical topology device, and SoundVolumeView
    lists both a '\\Device\\' row and '\\Subunit\\' row for the same
    physical mic. Only the '\\Device\\' one is a real default-device
    endpoint -- /SetDefault against a '\\Subunit\\' ID can silently no-op
    (exit 0, no stdout/stderr, no actual switch), which is exactly the
    silent-restore-failure this distinction exists to avoid."""
    return "\\device\\" in (row.get("Command-Line Friendly ID") or "").lower()


def find_switcher_row(rows, target_name, kind):
    """Find the SoundVolumeView row for a device matching target_name,
    restricted to the right direction (Capture=input, Render=output).
    Matches on normalized substring containment in either direction
    against both the 'Name' and 'Device Name' columns, since we don't
    know in advance which one lines up with what WASAPI/PortAudio gave
    us. Only rows with a non-empty Command-Line Friendly ID are
    considered -- rows without one are per-application volume entries,
    not actual devices.

    When multiple rows match the same device name, a '\\Device\\' row is
    preferred over a '\\Subunit\\' row (see _is_device_id)."""
    direction = "Capture" if kind == "input" else "Render"
    target_norm = _normalize(target_name)
    if not target_norm:
        return None
    candidates = [r for r in rows
                  if r.get("Direction") == direction and r.get("Command-Line Friendly ID")]

    def best_match(matches):
        if not matches:
            return None
        matches.sort(key=lambda r: 0 if _is_device_id(r) else 1)
        return matches[0]

    name_matches = [row for row in candidates
                     if (row_norm := _normalize(row.get("Name")))
                     and (row_norm in target_norm or target_norm in row_norm)]
    row = best_match(name_matches)
    if row:
        return row

    dev_matches = [row for row in candidates
                    if (dev_norm := _normalize(row.get("Device Name")))
                    and (dev_norm in target_norm or target_norm in dev_norm)]
    return best_match(dev_matches)


def get_default_input_name():
    """Return the name of Windows' current default recording device,
    preferring WASAPI since it reports full (untruncated) names."""
    try:
        hostapis = sd.query_hostapis()
        wasapi_index = next((i for i, h in enumerate(hostapis) if "wasapi" in h["name"].lower()), None)
        if wasapi_index is not None:
            wasapi_default = hostapis[wasapi_index]["default_input_device"]
            if wasapi_default is not None and wasapi_default >= 0:
                return sd.query_devices(wasapi_default)["name"]
    except Exception:
        pass
    try:
        default_index = sd.default.device[0]
        if default_index is None or default_index < 0:
            return None
        return sd.query_devices(default_index)["name"]
    except Exception:
        return None


def resolve_full_input_name(raw_name):
    """Given a (possibly truncated) input device name, try to find its
    full, untruncated WASAPI name so tools like SoundVolumeView can match
    it reliably. Falls back to the raw name if no WASAPI match is found."""
    if not raw_name:
        return raw_name
    matches = find_device_wasapi(raw_name, "input")
    if matches:
        return matches[0][1]
    return raw_name


def set_default_input(switcher_path, device_name, delay=0.3):
    """Switch Windows' default recording device across all three roles.
    Order matters: the watchdog (see start_watchdog below) sets the role
    apps like Zoom actually use (2 = Communications) FIRST, then the ones
    that only affect the Sound settings display (1 = Multimedia,
    0 = Console) -- that way, if it gets cut off, it's the cosmetic roles
    that are missed, not the one that matters for calls.

    A short pause between calls matters too: firing off three back-to-back
    SoundVolumeView process launches with no gap between them can outrun
    Windows' audio service, so only the first one reliably commits. Pass
    delay=0 for time-critical callers (like the watchdog) where speed
    matters more than that safety margin."""
    if not switcher_path or not device_name:
        return False
    try:
        roles = ("2", "1", "0")
        for i, role in enumerate(roles):
            result = subprocess.run(
                [switcher_path, "/SetDefault", device_name, role],
                check=False, capture_output=True, text=True,
            )
            print(f"  [DIAG] role {role}: exit={result.returncode} "
                  f"stdout={result.stdout!r} stderr={result.stderr!r}")
            if delay and i < len(roles) - 1:
                time.sleep(delay)
        return True
    except Exception as e:
        print(f"Warning: could not switch default microphone ({e})")
        return False


def start_watchdog(switcher_path, mic_file):
    """Launch watchdog.py as a fully DETACHED background process -- not
    attached to this console window, so closing the window (which kills
    everything attached to it almost instantly) can't kill this too. It
    waits for this process to exit and restores the mic if we didn't
    already do it cleanly ourselves."""
    watchdog_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog.py")
    if not os.path.exists(watchdog_script):
        print("Note: watchdog.py not found -- mic restore on abrupt close won't be guaranteed.")
        return
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            [sys.executable, watchdog_script, str(os.getpid()), switcher_path, mic_file],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
        )
    except Exception as e:
        print(f"Note: couldn't start the watchdog process ({e}).")


def play_startup_sound():
    """Little victory chime once the denoiser is up and running."""
    try:
        for freq, dur in ((523, 100), (659, 100), (784, 150), (1047, 250)):
            winsound.Beep(freq, dur)
    except Exception:
        pass


def play_shutdown_sound():
    """Little descending chime when the denoiser stops."""
    try:
        for freq, dur in ((784, 120), (659, 120), (523, 200)):
            winsound.Beep(freq, dur)
    except Exception:
        pass


def get_devices():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_config.txt")

    cable_matches = find_device("CABLE Input", "output")
    if not cable_matches:
        print("ERROR: Could not find 'CABLE Input' device.")
        print("Make sure VB-CABLE is installed: https://vb-audio.com/Cable/")
        raise SystemExit(1)
    output_device = cable_matches[0][0]

    if os.path.exists(config_path):
        with open(config_path) as f:
            saved = f.read().strip()
        if saved.isdigit():
            saved_index = int(saved)
            devices = sd.query_devices()
            if saved_index < len(devices) and devices[saved_index]["max_input_channels"] > 0:
                print(f"Using saved microphone: {devices[saved_index]['name']}")
                return saved_index, output_device

    mic_index = choose_microphone()
    with open(config_path, "w") as f:
        f.write(str(mic_index))
    return mic_index, output_device


class Denoiser:
    """All the state and logic for the mic-switching denoiser, packaged so
    a GUI (or anything else) can call setup() once, then start()/stop() as
    many times as it wants -- nothing here runs just from importing this
    module. See gui.py for the button that drives this."""

    def __init__(self):
        self.input_device = None
        self.output_device = None
        self.mic_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "original_mic.txt")
        self.switcher_path = None
        self.switcher_rows = []
        self.original_mic_name = None
        self.original_mic_id = None
        self.cable_output_id = None
        self.cable_output_display = "CABLE Output"
        self.model = None
        self.df_state = None
        self.input_queue = queue.Queue(maxsize=2)
        self.output_queue = queue.Queue(maxsize=2)
        self.silence = np.zeros(BLOCK_SIZE, dtype=np.float32)
        self.stream = None
        self.is_on = False
        self._worker_thread = None

        # Diagnostics only -- track where audio might be getting lost
        # (model too slow to keep up in real time vs. something else).
        self._chunks_processed = 0
        self._chunks_over_budget = 0
        self._worst_ms = 0.0
        self._input_drops = 0
        self._output_empties = 0
        self._diag_window = 0

    def setup(self, log=print):
        """One-time setup: pick audio devices, resolve the switcher IDs for
        CABLE Output and the current mic, and load the DeepFilterNet2
        model. Doesn't touch the default mic or open an audio stream yet --
        that only happens in start(). Safe to call before any GUI exists;
        may block on console input() the first time (picking a mic)."""
        self.input_device, self.output_device = get_devices()

        self.switcher_path = get_switcher_path()
        if self.switcher_path:
            self.switcher_rows = query_switcher_devices(self.switcher_path)
            log(f"  [DIAG] SoundVolumeView CSV dump returned {len(self.switcher_rows)} rows.")

            self.original_mic_name = get_default_input_name()
            if self.original_mic_name and "cable" in self.original_mic_name.lower():
                # Windows' default is already pointed at the virtual cable --
                # almost certainly a leftover from a previous run that didn't
                # restore cleanly. Fall back to whichever real microphone was
                # actually selected for input instead.
                log("Note: default mic was already set to a CABLE device (likely left over from a previous run).")
                raw_mic_name = sd.query_devices(self.input_device)["name"]
                self.original_mic_name = resolve_full_input_name(raw_mic_name)
                log(f"Will restore to your selected microphone instead: '{self.original_mic_name}'")

            original_mic_row = (find_switcher_row(self.switcher_rows, self.original_mic_name, "input")
                                 if self.original_mic_name else None)
            if original_mic_row:
                self.original_mic_id = original_mic_row["Command-Line Friendly ID"]
                id_kind = "Device" if _is_device_id(original_mic_row) else "Subunit"
                log(f"  [DIAG] original mic '{self.original_mic_name}' -> switcher ID "
                    f"'{self.original_mic_id}' ({id_kind})")
            else:
                self.original_mic_id = self.original_mic_name
                if self.original_mic_name:
                    log(f"  [DIAG] warning: no switcher-CSV match for original mic "
                        f"'{self.original_mic_name}'; restore may silently no-op.")

            cable_row = find_switcher_row(self.switcher_rows, "CABLE Output", "input")
            if cable_row:
                self.cable_output_id = cable_row["Command-Line Friendly ID"]
                self.cable_output_display = cable_row.get("Name", "CABLE Output")
            else:
                log("Warning: couldn't find 'CABLE Output' in the SoundVolumeView device list.")
                log("You'll need to manually select it as your mic in Zoom.")
        else:
            log("Note: SoundVolumeView not found, so the default microphone won't be switched automatically.")
            log("In Zoom, manually select 'CABLE Output (VB-Audio Virtual Cable)' as your microphone.")

        log("Loading DeepFilterNet2 model...")
        self.model, self.df_state, _ = init_df(model_base_dir="DeepFilterNet2")
        self.model.eval()
        log("Model loaded.")

        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def _worker(self):
        while True:
            mono = self.input_queue.get()
            if mono is None:
                break

            start = time.perf_counter()
            audio_tensor = torch.from_numpy(mono).unsqueeze(0)
            with torch.no_grad():
                enhanced = enhance(self.model, self.df_state, audio_tensor)
            elapsed_ms = (time.perf_counter() - start) * 1000

            self._chunks_processed += 1
            if elapsed_ms > BUDGET_MS:
                self._chunks_over_budget += 1
            self._worst_ms = max(self._worst_ms, elapsed_ms)

            enhanced_np = enhanced.squeeze(0).cpu().numpy()

            if len(enhanced_np) < BLOCK_SIZE:
                enhanced_np = np.pad(enhanced_np, (0, BLOCK_SIZE - len(enhanced_np)))
            else:
                enhanced_np = enhanced_np[:BLOCK_SIZE]

            try:
                self.output_queue.put_nowait(enhanced_np)
            except queue.Full:
                pass

            self._maybe_report_diag()

    def _maybe_report_diag(self):
        """Print a timing/drop summary roughly every ~2s of audio (50
        chunks at 40ms each) instead of spamming a line per chunk."""
        self._diag_window += 1
        if self._diag_window < 50:
            return
        self._diag_window = 0
        pct_over = (100 * self._chunks_over_budget / self._chunks_processed) if self._chunks_processed else 0
        print(f"  [DIAG] budget={BUDGET_MS:.1f}ms  worst_chunk={self._worst_ms:.1f}ms  "
              f"over_budget={self._chunks_over_budget}/{self._chunks_processed} ({pct_over:.0f}%)  "
              f"input_drops={self._input_drops}  output_empties={self._output_empties}")

    def _callback(self, indata, outdata, frames, time_info, status):
        if status:
            print(status)

        mono = indata[:, 0].astype(np.float32).copy()

        try:
            self.input_queue.put_nowait(mono)
        except queue.Full:
            self._input_drops += 1

        try:
            enhanced_np = self.output_queue.get_nowait()
        except queue.Empty:
            enhanced_np = self.silence
            self._output_empties += 1

        outdata[:, 0] = enhanced_np
        if outdata.shape[1] > 1:
            for ch in range(1, outdata.shape[1]):
                outdata[:, ch] = enhanced_np

    def start(self, log=print):
        """Switch the default mic to CABLE Output and start streaming.
        This does blocking subprocess calls (~1s), so call it from a
        background thread if you don't want to freeze a GUI."""
        if self.is_on:
            return
        self._chunks_processed = 0
        self._chunks_over_budget = 0
        self._worst_ms = 0.0
        self._input_drops = 0
        self._output_empties = 0
        self._diag_window = 0
        if self.switcher_path and self.cable_output_id:
            log(f"Switching Windows default microphone to '{self.cable_output_display}'...")
            set_default_input(self.switcher_path, self.cable_output_id)

            # Write the mic to restore to a marker file, then hand off to a
            # detached watchdog process that guarantees the restore happens
            # even if this process gets killed abruptly (e.g. the console
            # or GUI process dying without a clean stop()).
            if self.original_mic_id:
                with open(self.mic_file, "w") as f:
                    f.write(self.original_mic_id)
                start_watchdog(self.switcher_path, self.mic_file)

        self.stream = sd.Stream(device=(self.input_device, self.output_device),
                                 samplerate=SAMPLE_RATE,
                                 blocksize=BLOCK_SIZE,
                                 dtype="float32",
                                 channels=1,
                                 callback=self._callback)
        self.stream.start()
        play_startup_sound()
        self.is_on = True
        log("Denoiser ON.")

    def stop(self, log=print):
        """Stop streaming and restore the original default mic."""
        if not self.is_on:
            return
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.switcher_path and self.original_mic_id:
            log(f"Restoring default microphone to '{self.original_mic_name}'...")
            set_default_input(self.switcher_path, self.original_mic_id)
            # Clean stop -- tell the watchdog it doesn't need to do anything.
            if os.path.exists(self.mic_file):
                try:
                    os.remove(self.mic_file)
                except Exception:
                    pass
        play_shutdown_sound()
        self.is_on = False
        log("Denoiser OFF.")

    def shutdown(self):
        """Full teardown -- call once when the whole app is exiting."""
        self.stop()
        self.input_queue.put(None)


if __name__ == "__main__":
    # Standalone console mode (no GUI) -- same behavior as before this was
    # split into a class. Mainly useful for testing without gui.py.
    denoiser = Denoiser()
    denoiser.setup()
    denoiser.start()
    print("Set your call app's microphone to 'CABLE Output (VB-Audio Virtual Cable)'.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        denoiser.shutdown()