import numpy as np
import sounddevice as sd
import torch

import df.logger
df.logger.get_commit_hash = lambda: "unknown"
df.logger.get_branch_name = lambda: "unknown"

from df import init_df, enhance

INPUT_DEVICE = 2
OUTPUT_DEVICE = None  # None = your default speakers/headphones
SAMPLE_RATE = 48000
BLOCK_SIZE = 4800

import os
torch.set_num_threads(os.cpu_count())

print("Loading DeepFilterNet2 model...")
model, df_state, _ = init_df(model_base_dir="DeepFilterNet2")
model.eval()
print("Model loaded. Talk into your mic -- you'll hear the cleaned version through your speakers.")
print("Use headphones to avoid feedback/echo. Press Ctrl+C to stop.\n")


import time
budget_ms = (BLOCK_SIZE / SAMPLE_RATE) * 1000
print(f"Real-time budget per chunk: {budget_ms:.1f} ms\n")


def callback(indata, outdata, frames, time_info, status):
    if status:
        print(status)

    start = time.perf_counter()

    mono = indata[:, 0].astype(np.float32)
    audio_tensor = torch.from_numpy(mono).unsqueeze(0)

    with torch.no_grad():
        enhanced = enhance(model, df_state, audio_tensor)

    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms > budget_ms:
        print(f"OVER BUDGET: took {elapsed_ms:.1f}ms, budget was {budget_ms:.1f}ms")

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