"""Diagnostic only: same pipeline as main.py (same saved mic, same
sample rate/block size, same model), but sends output straight to your
default speakers/headphones instead of CABLE Input. Use this to check
whether the model is actually producing correct, audible output at all,
independent of CABLE/Zoom -- if you hear your own denoised voice here,
the DSP pipeline is fully proven and the bug is specifically in the
CABLE Input write or Zoom's read from CABLE Output. If you don't hear
anything here either, the bug is upstream of CABLE entirely.

Use headphones to avoid feedback/echo. Press Ctrl+C to stop.
"""
import os
import time

import numpy as np
import sounddevice as sd
import torch

import df.logger
df.logger.get_commit_hash = lambda: "unknown"
df.logger.get_branch_name = lambda: "unknown"

from df import init_df, enhance

SAMPLE_RATE = 48000
BLOCK_SIZE = 1920
BUDGET_MS = (BLOCK_SIZE / SAMPLE_RATE) * 1000
OUTPUT_DEVICE = None  # None = your default playback device (speakers/headphones)

torch.set_num_threads(2)  # benchmarked: fewer threads = lower latency and CPU
                          # usage for chunks this small; os.cpu_count() thrashes


def get_saved_mic_index():
    """Read the same device_config.txt main.py uses, so this test uses
    your actual current mic instead of a stale hardcoded index."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_config.txt")
    if not os.path.exists(config_path):
        raise SystemExit("No device_config.txt found -- run main.py or gui.py once first to pick a mic.")
    with open(config_path) as f:
        saved = f.read().strip()
    if not saved.isdigit():
        raise SystemExit(f"device_config.txt didn't contain a plain device index (got: {saved!r}).")
    index = int(saved)
    device = sd.query_devices(index)
    if device["max_input_channels"] <= 0:
        raise SystemExit(f"Saved device index {index} ('{device['name']}') isn't an input device -- "
                          f"delete device_config.txt and re-run main.py/gui.py to re-pick your mic.")
    return index, device["name"]


INPUT_DEVICE, mic_name = get_saved_mic_index()
print(f"Using saved microphone: {mic_name}")

print("Loading DeepFilterNet2 model...")
model, df_state, _ = init_df(model_base_dir="DeepFilterNet2")
model.eval()
print("Model loaded. Talk into your mic -- you'll hear the cleaned version through your speakers.")
print("Use headphones to avoid feedback/echo. Press Ctrl+C to stop.\n")


def callback(indata, outdata, frames, time_info, status):
    if status:
        print(status)

    start = time.perf_counter()
    mono = indata[:, 0].astype(np.float32)
    audio_tensor = torch.from_numpy(mono).unsqueeze(0)

    with torch.no_grad():
        enhanced = enhance(model, df_state, audio_tensor)

    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms > BUDGET_MS:
        print(f"  [DIAG] over budget: {elapsed_ms:.1f}ms (budget {BUDGET_MS:.1f}ms)")

    enhanced_np = enhanced.squeeze(0).cpu().numpy()

    if len(enhanced_np) < frames:
        enhanced_np = np.pad(enhanced_np, (0, frames - len(enhanced_np)))
    else:
        enhanced_np = enhanced_np[:frames]

    outdata[:, 0] = enhanced_np
    if outdata.shape[1] > 1:
        for ch in range(1, outdata.shape[1]):
            outdata[:, ch] = enhanced_np


try:
    with sd.Stream(device=(INPUT_DEVICE, OUTPUT_DEVICE),
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype="float32",
                    channels=1,
                    callback=callback):
        while True:
            sd.sleep(1000)
except KeyboardInterrupt:
    print("\nStopped.")