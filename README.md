# Voice Denoiser

A Windows real-time microphone denoiser built around
[DeepFilterNet2](https://github.com/Rikorose/DeepFilterNet). The denoiser
captures audio from a selected microphone, removes background noise, and
routes the cleaned microphone signal through VB-CABLE so applications such as
Zoom can use it as their microphone.

## How it works

```text
Physical microphone
        |
        v
DeepFilterNet2 denoising
        |
        v
VB-CABLE (CABLE Output)
        |
        v
Zoom / Discord / other applications
```

The project uses a 48 kHz sample rate and 1,920-sample audio blocks. This
gives the processing callback approximately 40 ms per block to keep up with
real-time audio.

## Requirements

- Windows 10 or Windows 11
- Python 3.11
- A microphone and playback device
- [VB-CABLE](https://vb-audio.com/Cable/)
- SoundVolumeView or `svcl` for switching the Windows default recording device
- Python packages used by the project:
  - `numpy`
  - `sounddevice`
  - `torch` (CPU build is sufficient)
  - `deepfilternet`

## Installation

1. Open **PowerShell as Administrator**.
2. Run the setup script:

   ```powershell
   .\install.ps1
   ```

3. Restart Windows after VB-CABLE has been installed.
4. If the setup script cannot find `requirements.txt`, install the packages
   manually with Python 3.11:

   ```powershell
   py -3.11 -m pip install numpy sounddevice deepfilternet `
       --index-url https://download.pytorch.org/whl/cpu
   py -3.11 -m pip install torch `
       --index-url https://download.pytorch.org/whl/cpu
   ```

   If your package index already provides the required PyTorch build, the
   packages can also be installed without the `--index-url` option.

Keep `SoundVolumeView.exe` beside the Python files, or install the console
version (`svcl`) and make sure it is available on `PATH`.

## Running the denoiser

The recommended entry point is the GUI:

```powershell
py -3.11 gui.py
```

You can also double-click `run.bat`.

On first startup:

1. Choose a physical microphone when prompted.
2. The selected device index is saved in `device_config.txt`.
3. Wait for the DeepFilterNet2 model to load.
4. Click **DENOISER: ON**.
5. In your calling application, select:
   **CABLE Output (VB-Audio Virtual Cable)** as the microphone.

Click **DENOISER: OFF** when finished. Closing the GUI restores the previous
Windows microphone where possible.

Use headphones while testing to prevent feedback and echo.

## Audio-path diagnostic

`monitor_test.py` runs the same microphone selection, sample rate, block size,
and DeepFilterNet2 processing as the main denoiser, but sends the cleaned
signal directly to the default speakers or headphones instead of VB-CABLE.

Run it after selecting a microphone through `gui.py` or `main.py`:

```powershell
py -3.11 monitor_test.py
```

Speak into the microphone and listen for the denoised output. Press
`Ctrl+C` to stop.

- If you hear your cleaned voice, the DeepFilterNet2 pipeline works and the
  problem is likely in VB-CABLE or the calling application's microphone
  selection.
- If you hear nothing, investigate the physical microphone, device selection,
  audio permissions, or model/runtime setup before troubleshooting VB-CABLE.

## Troubleshooting

### `No device_config.txt found`

Run `py -3.11 gui.py`, choose a microphone, and try again. The diagnostic
script intentionally uses the saved device selection.

### The saved device is no longer valid

Delete `device_config.txt`, then start the GUI and select the microphone
again.

### VB-CABLE is missing

Reinstall VB-CABLE, restart Windows, and confirm that both **CABLE Input** and
**CABLE Output** appear in Windows sound settings.

### Applications do not receive cleaned audio

Confirm that the application input is set to
**CABLE Output (VB-Audio Virtual Cable)** and run `monitor_test.py`. If the
monitor test works, the denoising pipeline is healthy and the issue is in the
virtual-device or application configuration.

### Audio is delayed or processing is over budget

The scripts print a diagnostic when model processing takes longer than the
available audio-block budget. Close CPU-intensive applications and verify that
the CPU PyTorch build is installed. The project limits PyTorch to two threads
for lower latency on these small blocks.

## Utility scripts

| File | Purpose |
| --- | --- |
| `gui.py` | Starts the ON/OFF control panel |
| `main.py` | Core denoiser, device switching, and VB-CABLE routing |
| `monitor_test.py` | Tests denoising directly to the default playback device |
| `debug_devices.py` | Inspects SoundVolumeView device identifiers |
| `bench_threads.py` | Benchmarks DeepFilterNet2 inference thread counts |
| `test_local.py` | Simple direct microphone-to-speaker denoising test |
| `install.ps1` | Installs Windows and Python prerequisites |

## Stopping safely

Use the GUI's **DENOISER: OFF** button before closing the window. For
`monitor_test.py`, press `Ctrl+C`.
