"""Diagnostic only: benchmarks DeepFilterNet2 inference speed across
different torch thread counts and block sizes, using synthetic (random
noise) audio instead of the mic -- so you don't have to sit there talking
for every combination.

This tells us which (threads, block_size) combo actually stays under budget
on this CPU, so main.py/gui.py can be tuned instead of guessed at.

Usage:
    py -3.11 bench_threads.py
"""
import os
import time

import numpy as np
import torch

import df.logger
df.logger.get_commit_hash = lambda: "unknown"
df.logger.get_branch_name = lambda: "unknown"

from df import init_df, enhance

SAMPLE_RATE = 48000
THREAD_COUNTS = sorted(set([1, 2, 4, os.cpu_count()]))
BLOCK_SIZES = [1920, 2880, 3840, 4800]  # 40ms, 60ms, 80ms, 100ms @ 48kHz
WARMUP_ITERS = 3
TIMED_ITERS = 15

print("Loading DeepFilterNet2 model...")
model, df_state, _ = init_df(model_base_dir="DeepFilterNet2")
model.eval()
print("Model loaded.\n")

print(f"{'threads':>7} | {'block':>6} | {'budget_ms':>9} | {'avg_ms':>7} | {'worst_ms':>8} | {'over_budget'}")
print("-" * 70)

for block_size in BLOCK_SIZES:
    budget_ms = (block_size / SAMPLE_RATE) * 1000
    audio = np.random.randn(block_size).astype(np.float32) * 0.05

    for threads in THREAD_COUNTS:
        torch.set_num_threads(threads)
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)

        # Warmup -- first calls are slower (lazy init, cache warmup) and
        # would skew the timing if counted.
        for _ in range(WARMUP_ITERS):
            with torch.no_grad():
                enhance(model, df_state, audio_tensor)

        times = []
        for _ in range(TIMED_ITERS):
            start = time.perf_counter()
            with torch.no_grad():
                enhance(model, df_state, audio_tensor)
            times.append((time.perf_counter() - start) * 1000)

        avg_ms = sum(times) / len(times)
        worst_ms = max(times)
        over_budget = sum(1 for t in times if t > budget_ms)

        print(f"{threads:>7} | {block_size:>6} | {budget_ms:>9.1f} | {avg_ms:>7.1f} | "
              f"{worst_ms:>8.1f} | {over_budget}/{TIMED_ITERS}")

print("\nLook for the row with the lowest 'worst_ms' relative to its 'budget_ms',")
print("and ideally 0 in 'over_budget'. That's the (threads, block_size) to use.")
