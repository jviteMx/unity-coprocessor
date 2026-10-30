# Build and validation — October 30, 2026

The firmware was cross-compiled for the Adafruit MatrixPortal ESP32-S3 with `espressif32@6.9.0`, Arduino ESP32 2.0.17, and the Xtensa ESP32-S3 8.4.0 toolchain. All three example configurations built successfully. The packaged artifacts and generated header are restored to the default `eeg19-existing.json` configuration.

| Configuration | Static RAM reported by linker | Application flash reported by linker |
|---|---:|---:|
| 19 EEG, acquired reference | 21,680 bytes | 278,805 bytes |
| 20 EEG, included A2 reference | 21,808 bytes | 278,813 bytes |
| 32 EEG, average reference | 23,344 bytes | 279,417 bytes |

These static RAM figures exclude the heap-allocated DSP engine and history buffer. At 500 Hz and four seconds, the history alone uses 152,000 / 160,000 / 256,000 bytes respectively. History allocation prefers PSRAM. A successful cross-compile does not demonstrate on-device allocation or throughput.

`tests/test_system.py`: **10 tests passed**, including:

- Causal filter and Welch band powers versus SciPy (relative tolerance 0.2%, absolute tolerance 0.002 µV²).
- 19-, 20-, and 32-electrode ICA calibration, correct reference rank reduction, complete warmup, and finite features.
- Attenuation of a deliberately frontal synthetic source under the frontal-projection policy. This is an algorithm test, not validation of ocular artifact recognition in people.
- Flat-data ICA failure, amplitude gating, zero-denominator handling, expression validation, and configuration errors.
- Actual firmware `setup()`/`loop()` compiled with Linux serial/allocator shims: config handshake, emitted JSON and mean, CRC rejection, duplicate sequence rejection, and history reset after a timestamp gap.
- Incoming LSL label/unit/rate mapping, USB packet layout and CRC, fragmented host response reads, and firmware errors.

Native C++ tests run with undefined-behavior instrumentation. They do not emulate ESP32 timing, caches, USB hardware, RTOS scheduling, or PSRAM. The Unity receiver source has not been compiled in the Unity editor here.

`tests/lsl_smoke.py`: **passed**, receiving 512 samples from a real local 19-channel, 500 Hz synthetic LSL outlet, inspecting metadata, converting units, and creating USB packets. Local socket access was enabled for this check. This does not exercise a physical USB connection.

`tests/replay_native.py`: the existing 90-second, 45,000-sample recording produced **109 feature windows**, of which **43** passed the default amplitude gate. Calibration retained **8** ICA dimensions under the configured relative eigenvalue cutoff. The default 0.35 frontal-fraction threshold selected **no component for removal**. Therefore this replay does not demonstrate successful artifact cleaning; it demonstrates finite streaming processing and explicit quality/selection reporting. The first history ended at source offset 35.998 seconds. The machine-readable summary is `artifacts/native-replay-summary.json`.

The flash helper's dry run verified all packaged image checksums and configuration identity and generated offsets matching PlatformIO: bootloader 0x0, partition table 0x8000, boot selection 0xe000, application 0x10000, TinyUF2 0x410000.

No ESP32 serial device was attached in this environment. The binaries have **not** been flashed or run on hardware. Sustained 500 Hz USB throughput, worst-case computation time, actual RAM allocation, USB re-enumeration, ICA suitability on live EEG, and Unity runtime behavior remain to be checked with the connected board and headset. The bridge stops on stale backlog or protocol failure; these checks must not be substituted for measured on-device results.

Reproduce from this folder with:

```bash
python tests/test_system.py
python tests/lsl_smoke.py
python tests/replay_native.py
python tools/build.py --config config/eeg19-existing.json
python tools/flash.py --port /dev/ttyACM0 --dry-run
```

The packaged `artifacts/manifest.json` records config identity, source-file hashes, and image hashes for this exact build. Research/documentation references and implementation choices are linked in `README.md`.
