"""Generate a realistic synthetic array-radar run for spviz."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import spviz


def generate(output: Path, seed: int = 7) -> Path:
    recorder = spviz.init(
        output,
        name="Synthetic phased-array radar",
        metadata={"seed": seed, "description": "FMCW-style array processing example"},
    )
    rng = np.random.default_rng(seed)
    receivers, chirps, samples = 8, 32, 64
    look_angles = np.linspace(-60.0, 60.0, 25)
    receiver_positions = np.arange(receivers) * 0.5
    chirp_times_ms = np.arange(chirps) / 4.0
    sample_times_us = np.arange(samples) / 2.0
    ranges_m = np.arange(samples) * 0.75
    velocities_mps = np.linspace(-12.0, 12.0, chirps, endpoint=False)
    channel = np.arange(receivers)[:, None, None]
    slow_time = np.arange(chirps)[None, :, None]
    fast_time = np.arange(samples)[None, None, :]

    # Fractional bins create realistic leakage rather than ideal single-bin peaks.
    targets = [
        {"range_bin": 11.4, "doppler_bin": 5.3, "angle_deg": -24.0, "amplitude": 1.0},
        {"range_bin": 29.7, "doppler_bin": -7.1, "angle_deg": 13.0, "amplitude": 0.58},
        {"range_bin": 46.2, "doppler_bin": 1.4, "angle_deg": 38.0, "amplitude": 0.34},
    ]
    iq = np.zeros((receivers, chirps, samples), dtype=np.complex64)
    for target in targets:
        scintillation = 1.0 + 0.05 * np.sin(0.71 * slow_time + target["range_bin"])
        phase = (
            2 * np.pi * target["range_bin"] * fast_time / samples
            + 2 * np.pi * target["doppler_bin"] * slow_time / chirps
            + np.pi * channel * np.sin(np.deg2rad(target["angle_deg"]))
        )
        iq += target["amplitude"] * scintillation * np.exp(1j * phase)

    # Low-Doppler distributed clutter and static leakage.
    for clutter_range, clutter_angle, amplitude in [
        (4.2, -42, 0.12),
        (18.6, 3, 0.08),
        (36.1, 31, 0.06),
    ]:
        phase = (
            2 * np.pi * clutter_range * fast_time / samples
            + np.pi * channel * np.sin(np.deg2rad(clutter_angle))
        )
        iq += amplitude * np.exp(1j * phase)

    gain = rng.normal(1.0, 0.025, receivers)[:, None, None]
    phase_error = rng.normal(0.0, np.deg2rad(2.0), receivers)[:, None, None]
    iq *= gain * np.exp(1j * phase_error)
    noise = rng.normal(size=iq.shape) + 1j * rng.normal(size=iq.shape)
    iq += (0.075 / np.sqrt(2) * noise).astype(np.complex64)
    spviz.tap(
        iq,
        "Channel I/Q",
        views={"Amplitude": {}, "Phase": {"representation": "phase"}},
        primary_view="Amplitude",
        axes=["receiver", "chirp", "fast-time sample"],
        coordinates={
            "receiver": {"values": receiver_positions, "units": "wavelengths"},
            "chirp": {"values": chirp_times_ms, "units": "ms"},
            "fast-time sample": {"values": sample_times_us, "units": "µs"},
        },
        units="normalized voltage",
        scale="linear",
        vmin=float(np.percentile(np.abs(iq), 18)),
        vmax=float(np.percentile(np.abs(iq), 99.8)),
        metadata={
            "description": "Calibrated complex ADC samples with thermal noise and distributed clutter."
        },
    )

    steering = np.exp(
        -1j
        * np.pi
        * np.sin(np.deg2rad(look_angles[:, None]))
        * np.arange(receivers)[None, :]
    )
    beamformed = np.einsum("ac,cps->aps", steering, iq, optimize=True) / receivers
    spviz.tap(
        beamformed,
        "Beamformed I/Q",
        views={"Amplitude": {}, "Phase": {"representation": "phase"}},
        primary_view="Amplitude",
        axes=["look angle", "chirp", "fast-time sample"],
        coordinates={
            "look angle": {"values": look_angles, "units": "deg"},
            "chirp": {"values": chirp_times_ms, "units": "ms"},
            "fast-time sample": {"values": sample_times_us, "units": "µs"},
        },
        operation="steer + coherent sum",
        scale="log",
        vmin=float(np.percentile(np.abs(beamformed), 35)),
        vmax=float(np.percentile(np.abs(beamformed), 99.8)),
        inputs=iq,
        metadata={
            "look_angles_deg": look_angles,
            "description": "Twenty-five conventional beamformer look directions.",
        },
    )

    range_window = np.hanning(samples)
    range_spectrum = np.fft.fft(beamformed * range_window[None, None, :], axis=2)
    doppler_window = np.hanning(chirps)
    range_doppler = np.fft.fftshift(
        np.fft.fft(range_spectrum * doppler_window[None, :, None], axis=1), axes=1
    )
    power = (np.abs(range_doppler) ** 2).astype(np.float32)
    spviz.tap(
        power,
        "Range–Doppler power",
        axes=["look angle", "Doppler bin", "range bin"],
        coordinates={
            "look angle": {"values": look_angles, "units": "deg"},
            "Doppler bin": {"values": velocities_mps, "units": "m/s"},
            "range bin": {"values": ranges_m, "units": "m"},
        },
        operation="Hann windows + range FFT + Doppler FFT",
        inputs=beamformed,
        units="power",
        scale="log",
        vmin=float(np.percentile(power, 55)),
        vmax=float(np.percentile(power, 99.9)),
        metadata={
            "description": "Windowed and Doppler-centered range–Doppler power volume."
        },
    )

    # Compact 2D cell-averaging CFAR approximation per angle plane.
    # Edge cells lack a complete training window.  Keep them explicitly
    # undefined instead of recording a physically meaningful-looking zero.
    cell_average = np.full_like(power, np.nan, dtype=np.float32)
    detections = np.zeros_like(power, dtype=np.uint8)
    guard_d, guard_r, train_d, train_r = 1, 1, 3, 5
    for angle_index, plane in enumerate(power):
        for doppler_index in range(train_d, chirps - train_d):
            for range_index in range(train_r, samples - train_r):
                region = plane[
                    doppler_index - train_d : doppler_index + train_d + 1,
                    range_index - train_r : range_index + train_r + 1,
                ].copy()
                region[
                    train_d - guard_d : train_d + guard_d + 1,
                    train_r - guard_r : train_r + guard_r + 1,
                ] = np.nan
                local_average = np.nanmean(region)
                cell_average[angle_index, doppler_index, range_index] = local_average
                threshold = local_average * 18.0
                if plane[doppler_index, range_index] > threshold:
                    detections[angle_index, doppler_index, range_index] = 1

    valid_cell_average = cell_average[np.isfinite(cell_average)]
    spviz.tap(
        cell_average,
        "Cell-average noise estimate",
        axes=["look angle", "Doppler bin", "range bin"],
        coordinates={
            "look angle": {"values": look_angles, "units": "deg"},
            "Doppler bin": {"values": velocities_mps, "units": "m/s"},
            "range bin": {"values": ranges_m, "units": "m"},
        },
        operation="CFAR training-cell average",
        inputs=power,
        units="power",
        scale="log",
        vmin=max(float(np.percentile(valid_cell_average, 25)), 1e-12),
        vmax=float(np.percentile(valid_cell_average, 99.8)),
        metadata={
            "description": "Local background-power estimate from CFAR training cells; edge cells without a complete training window are undefined."
        },
    )
    spviz.tap(
        detections,
        "CFAR detection mask",
        axes=["look angle", "Doppler bin", "range bin"],
        coordinates={
            "look angle": {"values": look_angles, "units": "deg"},
            "Doppler bin": {"values": velocities_mps, "units": "m/s"},
            "range bin": {"values": ranges_m, "units": "m"},
        },
        operation="CUT > 18 × cell average",
        inputs=[power, cell_average],
        units="binary",
        scale="linear",
        vmin=0,
        vmax=1,
        metadata={
            "description": "Binary CA-CFAR decisions: 1 is a detection and 0 is background."
        },
    )
    recorder.session.metadata["targets"] = targets
    recorder.close()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/radar-demo"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(generate(args.output, args.seed))
