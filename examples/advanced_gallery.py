"""Deterministic advanced signal-processing examples for the spviz gallery.

Each generator performs the processing it names and records physically labelled
intermediate products.  The simulations intentionally stay NumPy-only so the
examples remain inexpensive to build for GitHub Pages.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import numpy as np

import spviz


def _magnitude_limits(
    value: np.ndarray, low: float = 20, high: float = 99.8
) -> tuple[float, float]:
    finite = np.abs(np.asarray(value))
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return 0.0, 1.0
    lower, upper = np.percentile(finite, [low, high])
    if upper <= lower:
        upper = lower + max(1.0, abs(float(lower)) * 1e-6)
    return float(lower), float(upper)


def _trace_limits(
    value: np.ndarray, low: float = 0.5, high: float = 99.5
) -> tuple[float, float]:
    finite = np.asarray(value)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return 0.0, 1.0
    lower, upper = np.percentile(finite, [low, high])
    if upper <= lower:
        upper = lower + max(1.0, abs(float(lower)) * 1e-6)
    return float(lower), float(upper)


def _gps_ca_code(prn: int = 1) -> np.ndarray:
    """Return one bipolar GPS L1 C/A code period at one sample per chip."""
    # G2 phase-select taps, numbered from one as in IS-GPS-200.
    phase_select = {
        1: (2, 6),
        2: (3, 7),
        3: (4, 8),
        4: (5, 9),
        5: (1, 9),
        6: (2, 10),
        7: (1, 8),
        8: (2, 9),
        9: (3, 10),
        10: (2, 3),
    }
    if prn not in phase_select:
        raise ValueError(f"Unsupported demonstration PRN: {prn}")
    g1 = np.ones(10, dtype=np.uint8)
    g2 = np.ones(10, dtype=np.uint8)
    chips = np.empty(1023, dtype=np.float32)
    tap_a, tap_b = phase_select[prn]
    for index in range(1023):
        bit = g1[9] ^ g2[tap_a - 1] ^ g2[tap_b - 1]
        chips[index] = 1.0 if bit == 0 else -1.0
        g1_feedback = g1[2] ^ g1[9]
        g2_feedback = g2[1] ^ g2[2] ^ g2[5] ^ g2[7] ^ g2[8] ^ g2[9]
        g1[1:] = g1[:-1]
        g2[1:] = g2[:-1]
        g1[0] = g1_feedback
        g2[0] = g2_feedback
    return chips


def generate_gnss(path: Path) -> Path:
    """Generate a GPS L1 C/A acquisition run with delay, Doppler, and multipath."""
    seed = 71
    rng = np.random.default_rng(seed)
    prn = 1
    code = _gps_ca_code(prn)
    chips = len(code)
    integrations = 8
    sample_rate_hz = 1_023_000.0
    true_code_phase = 287
    true_doppler_hz = 1_250.0
    multipath_delay_chips = 17
    doppler_bins_hz = np.linspace(-2_500.0, 2_500.0, 41)
    samples = integrations * chips
    time_s = np.arange(samples) / sample_rate_hz

    direct = np.tile(np.roll(code, true_code_phase), integrations)
    echo = np.tile(np.roll(code, true_code_phase + multipath_delay_chips), integrations)
    carrier = np.exp(1j * (2 * np.pi * true_doppler_hz * time_s + 0.37))
    noise = rng.normal(size=samples) + 1j * rng.normal(size=samples)
    baseband = (
        (0.62 * direct + 0.18 * echo) * carrier + 0.92 / np.sqrt(2) * noise
    ).astype(np.complex64)

    recorder = spviz.init(
        path,
        name="GPS L1 C/A acquisition",
        metadata={
            "seed": seed,
            "prn": prn,
            "true_code_phase_chips": true_code_phase,
            "true_doppler_hz": true_doppler_hz,
            "multipath_delay_chips": multipath_delay_chips,
        },
    )
    spviz.tap(
        code,
        "PRN 1 local replica",
        axes=["code phase"],
        coordinates={"code phase": {"values": np.arange(chips), "units": "chips"}},
        units="bipolar chip",
        vmin=-1,
        vmax=1,
        metadata={
            "description": "The actual 1023-chip GPS L1 C/A Gold code for PRN 1."
        },
    )
    low, high = _magnitude_limits(baseband, 3, 99.7)
    spviz.tap(
        baseband,
        "Received chip-rate I/Q",
        axes=["time"],
        coordinates={"time": {"values": time_s * 1e3, "units": "ms"}},
        operation="code modulation + Doppler + AWGN + multipath",
        inputs=code,
        units="normalized voltage",
        vmin=low,
        vmax=high,
    )

    local_spectrum = np.conj(np.fft.fft(code))
    correlations = np.empty(
        (integrations, len(doppler_bins_hz), chips), dtype=np.float32
    )
    for integration in range(integrations):
        first = integration * chips
        segment = baseband[first : first + chips]
        segment_time = (first + np.arange(chips)) / sample_rate_hz
        wipeoff = np.exp(-2j * np.pi * doppler_bins_hz[:, None] * segment_time[None, :])
        correlated = np.fft.ifft(
            np.fft.fft(segment[None, :] * wipeoff, axis=1) * local_spectrum, axis=1
        )
        correlations[integration] = (np.abs(correlated) ** 2 / chips).astype(np.float32)

    integration_times_ms = (
        (np.arange(integrations) + 0.5) * chips / sample_rate_hz * 1e3
    )
    cube_coordinates = {
        "integration": {"values": integration_times_ms, "units": "ms"},
        "Doppler": {"values": doppler_bins_hz, "units": "Hz"},
        "code phase": {"values": np.arange(chips), "units": "chips"},
    }
    low, high = _magnitude_limits(correlations, 55, 99.995)
    spviz.tap(
        correlations,
        "Coherent correlation cube",
        axes=["integration", "Doppler", "code phase"],
        coordinates=cube_coordinates,
        operation="carrier wipeoff + FFT circular correlation",
        inputs=[baseband, code],
        units="correlation power",
        scale="log",
        vmin=max(low, 1e-9),
        vmax=max(high, float(correlations.max())),
    )

    acquisition = correlations.sum(axis=0)
    peak_doppler_index, peak_code_index = np.unravel_index(
        int(np.argmax(acquisition)), acquisition.shape
    )
    estimated_doppler_hz = float(doppler_bins_hz[peak_doppler_index])
    estimated_code_phase = int(peak_code_index)
    map_coordinates = {
        "Doppler": {"values": doppler_bins_hz, "units": "Hz"},
        "code phase": {"values": np.arange(chips), "units": "chips"},
    }
    low, _ = _magnitude_limits(acquisition, 45, 99.9)
    spviz.tap(
        acquisition,
        "Noncoherent acquisition map",
        axes=["Doppler", "code phase"],
        coordinates=map_coordinates,
        operation="sum correlation power across integrations",
        inputs=correlations,
        units="correlation power",
        scale="log",
        vmin=max(low, 1e-9),
        vmax=float(acquisition.max()),
        metadata={
            "estimated_doppler_hz": estimated_doppler_hz,
            "estimated_code_phase_chips": estimated_code_phase,
        },
    )

    doppler_cut = acquisition[:, peak_code_index]
    low, _ = _magnitude_limits(doppler_cut, 10, 99)
    spviz.tap(
        doppler_cut,
        "Doppler cut at acquisition peak",
        axes=["Doppler"],
        coordinates={"Doppler": {"values": doppler_bins_hz, "units": "Hz"}},
        operation="slice at estimated code phase",
        inputs=acquisition,
        units="correlation power",
        scale="log",
        vmin=max(low, 1e-9),
        vmax=float(doppler_cut.max()),
    )
    code_cut = acquisition[peak_doppler_index]
    low, _ = _magnitude_limits(code_cut, 20, 99.8)
    spviz.tap(
        code_cut,
        "Code-phase cut at acquisition peak",
        axes=["code phase"],
        coordinates={"code phase": {"values": np.arange(chips), "units": "chips"}},
        operation="slice at estimated Doppler",
        inputs=acquisition,
        units="correlation power",
        scale="log",
        vmin=max(low, 1e-9),
        vmax=float(code_cut.max()),
    )

    flattened = acquisition.ravel()
    background = flattened[np.arange(flattened.size) != int(np.argmax(flattened))]
    median = float(np.median(background))
    robust_sigma = 1.4826 * float(np.median(np.abs(background - median)))
    threshold = median + 10.0 * robust_sigma
    detection = (acquisition > threshold).astype(np.uint8)
    spviz.tap(
        detection,
        "Acquisition detection mask",
        axes=["Doppler", "code phase"],
        coordinates=map_coordinates,
        operation="robust background threshold",
        inputs=acquisition,
        units="binary",
        vmin=0,
        vmax=1,
        metadata={"threshold": threshold, "detection_cells": int(detection.sum())},
    )
    recorder.session.metadata.update(
        {
            "estimated_code_phase_chips": estimated_code_phase,
            "estimated_doppler_hz": estimated_doppler_hz,
            "code_phase_error_chips": estimated_code_phase - true_code_phase,
            "doppler_error_hz": estimated_doppler_hz - true_doppler_hz,
        }
    )
    recorder.close()
    return path


def _modified_shepp_logan(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Create a compact modified Shepp-Logan phantom without image libraries."""
    coordinates_mm = np.linspace(-100.0, 100.0, size)
    x, y = np.meshgrid(coordinates_mm / 100.0, coordinates_mm / 100.0)
    ellipses = (
        (1.0, 0.6900, 0.9200, 0.0000, 0.0000, 0.0),
        (-0.80, 0.6624, 0.8740, 0.0000, -0.0184, 0.0),
        (-0.20, 0.1100, 0.3100, 0.2200, 0.0000, -18.0),
        (-0.20, 0.1600, 0.4100, -0.2200, 0.0000, 18.0),
        (0.10, 0.2100, 0.2500, 0.0000, 0.3500, 0.0),
        (0.10, 0.0460, 0.0460, 0.0000, 0.1000, 0.0),
        (0.10, 0.0460, 0.0460, 0.0000, -0.1000, 0.0),
        (0.10, 0.0460, 0.0230, -0.0800, -0.6050, 0.0),
        (0.10, 0.0230, 0.0230, 0.0000, -0.6060, 0.0),
        (0.10, 0.0230, 0.0460, 0.0600, -0.6050, 0.0),
    )
    phantom = np.zeros((size, size), dtype=np.float64)
    for amplitude, radius_x, radius_y, center_x, center_y, angle_deg in ellipses:
        angle = np.deg2rad(angle_deg)
        shifted_x, shifted_y = x - center_x, y - center_y
        rotated_x = shifted_x * np.cos(angle) + shifted_y * np.sin(angle)
        rotated_y = -shifted_x * np.sin(angle) + shifted_y * np.cos(angle)
        phantom[(rotated_x / radius_x) ** 2 + (rotated_y / radius_y) ** 2 <= 1] += (
            amplitude
        )
    return np.clip(phantom, 0.0, None).astype(np.float32), coordinates_mm


def generate_ct(path: Path) -> Path:
    """Generate a parallel-beam CT filtered-backprojection pipeline."""
    seed = 73
    rng = np.random.default_rng(seed)
    image_size = 96
    detector_count = 136
    angle_count = 60
    phantom, image_coordinates_mm = _modified_shepp_logan(image_size)
    angles_deg = np.linspace(0.0, 180.0, angle_count, endpoint=False)
    field_radius_mm = 100.0
    detector_positions_mm = np.linspace(
        -np.sqrt(2) * field_radius_mm, np.sqrt(2) * field_radius_mm, detector_count
    )
    detector_spacing_mm = float(detector_positions_mm[1] - detector_positions_mm[0])
    pixel_spacing_mm = float(image_coordinates_mm[1] - image_coordinates_mm[0])
    x_mm, y_mm = np.meshgrid(image_coordinates_mm, image_coordinates_mm)
    weights = phantom.ravel().astype(np.float64)
    sinogram = np.zeros((angle_count, detector_count), dtype=np.float64)
    for angle_index, angle_deg in enumerate(angles_deg):
        angle = np.deg2rad(angle_deg)
        ray_coordinate = (x_mm * np.cos(angle) + y_mm * np.sin(angle)).ravel()
        detector_position = (
            ray_coordinate - detector_positions_mm[0]
        ) / detector_spacing_mm
        lower = np.floor(detector_position).astype(int)
        fraction = detector_position - lower
        valid_lower = (lower >= 0) & (lower < detector_count)
        valid_upper = (lower + 1 >= 0) & (lower + 1 < detector_count)
        projection = np.zeros(detector_count, dtype=np.float64)
        np.add.at(
            projection,
            lower[valid_lower],
            weights[valid_lower] * (1.0 - fraction[valid_lower]),
        )
        np.add.at(
            projection,
            (lower + 1)[valid_upper],
            weights[valid_upper] * fraction[valid_upper],
        )
        sinogram[angle_index] = projection * pixel_spacing_mm
    # Add weak measurement noise before filtering so the reconstruction is not idealized.
    sinogram += rng.normal(0.0, 0.0025 * float(sinogram.max()), size=sinogram.shape)
    sinogram = sinogram.astype(np.float32)

    recorder = spviz.init(
        path,
        name="Parallel-beam CT reconstruction",
        metadata={
            "seed": seed,
            "phantom": "modified Shepp-Logan",
            "projection_geometry": "parallel beam",
            "angles": angle_count,
        },
    )
    image_coordinates = {
        "y": {"values": image_coordinates_mm, "units": "mm"},
        "x": {"values": image_coordinates_mm, "units": "mm"},
    }
    spviz.tap(
        phantom,
        "Attenuation phantom",
        axes=["y", "x"],
        coordinates=image_coordinates,
        units="relative attenuation",
        vmin=0.01,
        vmax=float(phantom.max()),
        metadata={"description": "Ground-truth modified Shepp-Logan attenuation map."},
    )
    sinogram_coordinates = {
        "projection angle": {"values": angles_deg, "units": "deg"},
        "detector position": {"values": detector_positions_mm, "units": "mm"},
    }
    low, high = _magnitude_limits(sinogram, 8, 99.8)
    spviz.tap(
        sinogram,
        "Noisy projection sinogram",
        axes=["projection angle", "detector position"],
        coordinates=sinogram_coordinates,
        operation="parallel-beam Radon projection + detector noise",
        inputs=phantom,
        units="line-integrated attenuation",
        vmin=low,
        vmax=high,
    )

    fft_length = 1 << int(np.ceil(np.log2(2 * detector_count)))
    spatial_frequency = np.fft.rfftfreq(fft_length, d=detector_spacing_mm)
    ramp_response = (2.0 * np.abs(spatial_frequency)).astype(np.float32)
    spviz.tap(
        ramp_response,
        "Ram-Lak ramp response",
        axes=["spatial frequency"],
        coordinates={
            "spatial frequency": {"values": spatial_frequency, "units": "cycles/mm"}
        },
        operation="Ram-Lak filter design",
        units="gain",
        vmin=0,
        vmax=float(ramp_response.max()),
    )
    filtered_sinogram = np.fft.irfft(
        np.fft.rfft(sinogram, n=fft_length, axis=1) * ramp_response[None, :],
        n=fft_length,
        axis=1,
    )[:, :detector_count].astype(np.float32)
    low, high = _trace_limits(filtered_sinogram, 0.3, 99.7)
    spviz.tap(
        filtered_sinogram,
        "Ramp-filtered projections",
        axes=["projection angle", "detector position"],
        coordinates=sinogram_coordinates,
        operation="zero-padded detector FFT × Ram-Lak response",
        inputs=[sinogram, ramp_response],
        units="filtered attenuation",
        vmin=low,
        vmax=high,
    )

    contributions = np.empty((angle_count, image_size, image_size), dtype=np.float32)
    for angle_index, angle_deg in enumerate(angles_deg):
        angle = np.deg2rad(angle_deg)
        ray_coordinate = x_mm * np.cos(angle) + y_mm * np.sin(angle)
        contributions[angle_index] = np.interp(
            ray_coordinate.ravel(),
            detector_positions_mm,
            filtered_sinogram[angle_index],
            left=0.0,
            right=0.0,
        ).reshape(image_size, image_size)
    contributions *= np.float32(np.pi / angle_count)
    volume_coordinates = {
        "projection angle": {"values": angles_deg, "units": "deg"},
        **image_coordinates,
    }
    low, high = _trace_limits(contributions, 0.3, 99.7)
    spviz.tap(
        contributions,
        "Backprojection contribution volume",
        axes=["projection angle", "y", "x"],
        coordinates=volume_coordinates,
        operation="interpolate each filtered projection into image space",
        inputs=filtered_sinogram,
        units="attenuation contribution",
        vmin=low,
        vmax=high,
        metadata={
            "description": "One signed reconstruction contribution per projection angle."
        },
    )
    reconstruction = np.maximum(contributions.sum(axis=0), 0.0)
    reconstruction /= max(float(reconstruction.max()), np.finfo(np.float32).eps)
    normalized_phantom = phantom / max(float(phantom.max()), np.finfo(np.float32).eps)
    reconstruction_rmse = float(
        np.sqrt(np.mean((reconstruction - normalized_phantom) ** 2))
    )
    low, high = _magnitude_limits(reconstruction, 8, 99.8)
    spviz.tap(
        reconstruction,
        "Filtered-backprojection image",
        axes=["y", "x"],
        coordinates=image_coordinates,
        operation="sum angular backprojections",
        inputs=contributions,
        units="normalized attenuation",
        vmin=low,
        vmax=high,
        metadata={"rmse_vs_truth": reconstruction_rmse},
    )
    center_profile = reconstruction[image_size // 2]
    spviz.tap(
        center_profile,
        "Central attenuation profile",
        axes=["x"],
        coordinates={"x": {"values": image_coordinates_mm, "units": "mm"}},
        operation="center-row slice",
        inputs=reconstruction,
        units="normalized attenuation",
        vmin=0,
        vmax=float(center_profile.max()),
    )
    recorder.session.metadata["reconstruction_rmse"] = reconstruction_rmse
    recorder.close()
    return path


def generate_channelizer(path: Path) -> Path:
    """Generate a four-antenna wideband polyphase-channelizer run."""
    seed = 79
    rng = np.random.default_rng(seed)
    antennas = 4
    channels = 64
    taps_per_channel = 4
    frames = 128
    sample_rate_hz = 1_000_000.0
    tap_count = channels * taps_per_channel
    sample_count = (frames - 1) * channels + tap_count
    time_s = np.arange(sample_count) / sample_rate_hz

    tap_index = np.arange(tap_count) - (tap_count - 1) / 2
    prototype = np.sinc(tap_index / channels) * np.hamming(tap_count)
    prototype /= prototype.sum()
    prototype = prototype.astype(np.float32)

    sources = (
        {
            "frequency_hz": -187_500.0,
            "amplitude": 0.85,
            "angle_deg": -25.0,
            "first": 800,
            "last": 5_500,
        },
        {
            "frequency_hz": 125_000.0,
            "amplitude": 0.62,
            "angle_deg": 18.0,
            "first": 3_000,
            "last": 8_000,
        },
    )
    capture = np.zeros((antennas, sample_count), dtype=np.complex128)
    for source in sources:
        gate = np.zeros(sample_count)
        gate[source["first"] : min(source["last"], sample_count)] = 1.0
        waveform = (
            source["amplitude"]
            * gate
            * np.exp(2j * np.pi * source["frequency_hz"] * time_s)
        )
        spatial_phase = np.exp(
            1j * np.pi * np.arange(antennas) * np.sin(np.deg2rad(source["angle_deg"]))
        )
        capture += spatial_phase[:, None] * waveform[None, :]
    # A lower-level linear chirp exercises bins across the full observation.
    chirp_start_hz, chirp_stop_hz = -390_000.0, 340_000.0
    chirp_rate = (chirp_stop_hz - chirp_start_hz) / time_s[-1]
    chirp_phase = 2 * np.pi * (chirp_start_hz * time_s + 0.5 * chirp_rate * time_s**2)
    chirp = 0.34 * np.exp(1j * chirp_phase) * np.hanning(sample_count)
    chirp_spatial_phase = np.exp(
        1j * np.pi * np.arange(antennas) * np.sin(np.deg2rad(42.0))
    )
    capture += chirp_spatial_phase[:, None] * chirp[None, :]
    capture += (
        0.12
        / np.sqrt(2)
        * (rng.normal(size=capture.shape) + 1j * rng.normal(size=capture.shape))
    )
    capture = capture.astype(np.complex64)

    recorder = spviz.init(
        path,
        name="Wideband polyphase channelizer",
        metadata={
            "seed": seed,
            "sample_rate_hz": sample_rate_hz,
            "channel_count": channels,
            "stationary_sources": list(sources),
            "chirp": {
                "start_hz": chirp_start_hz,
                "stop_hz": chirp_stop_hz,
                "angle_deg": 42.0,
            },
        },
    )
    low, high = _trace_limits(prototype)
    spviz.tap(
        prototype,
        "Prototype low-pass taps",
        axes=["tap"],
        coordinates={"tap": np.arange(tap_count)},
        operation="windowed-sinc PFB design",
        units="coefficient",
        vmin=low,
        vmax=high,
    )
    capture_coordinates = {
        "antenna": {"values": np.arange(antennas), "units": "element"},
        "time": {"values": time_s * 1e3, "units": "ms"},
    }
    low, high = _magnitude_limits(capture, 10, 99.8)
    spviz.tap(
        capture,
        "Wideband antenna I/Q",
        axes=["antenna", "time"],
        coordinates=capture_coordinates,
        operation="intermittent emitters + swept interferer + AWGN",
        units="normalized voltage",
        vmin=low,
        vmax=high,
    )

    windows = np.lib.stride_tricks.sliding_window_view(capture, tap_count, axis=1)[
        :, ::channels, :
    ]
    windows = windows[:, :frames]
    polyphase_sum = (
        (windows * prototype[None, None, :])
        .reshape(antennas, frames, taps_per_channel, channels)
        .sum(axis=2)
    )
    channelized = np.fft.fftshift(np.fft.fft(polyphase_sum, axis=2), axes=2).astype(
        np.complex64
    )
    frame_times_ms = (
        (np.arange(frames) * channels + (tap_count - 1) / 2) / sample_rate_hz
    ) * 1e3
    channel_frequencies_khz = (
        np.fft.fftshift(np.fft.fftfreq(channels, 1 / sample_rate_hz)) / 1e3
    )
    cube_coordinates = {
        "antenna": {"values": np.arange(antennas), "units": "element"},
        "frame": {"values": frame_times_ms, "units": "ms"},
        "channel center": {"values": channel_frequencies_khz, "units": "kHz"},
    }
    low, high = _magnitude_limits(channelized, 45, 99.9)
    spviz.tap(
        channelized,
        "Channelized antenna cube",
        axes=["antenna", "frame", "channel center"],
        coordinates=cube_coordinates,
        operation="4-tap polyphase filter bank + 64-point FFT",
        inputs=[capture, prototype],
        units="spectral amplitude",
        scale="log",
        vmin=max(low, 1e-9),
        vmax=high,
    )
    integrated_power = np.mean(np.abs(channelized) ** 2, axis=0).astype(np.float32)
    map_coordinates = {
        "frame": {"values": frame_times_ms, "units": "ms"},
        "channel center": {"values": channel_frequencies_khz, "units": "kHz"},
    }
    low, high = _magnitude_limits(integrated_power, 45, 99.9)
    spviz.tap(
        integrated_power,
        "Integrated channel power",
        axes=["frame", "channel center"],
        coordinates=map_coordinates,
        operation="incoherent average across antennas",
        inputs=channelized,
        units="power",
        scale="log",
        vmin=max(low, 1e-12),
        vmax=high,
    )
    noise_floor = np.median(integrated_power, axis=1, keepdims=True)
    burst_mask = (integrated_power > 7.0 * np.maximum(noise_floor, 1e-12)).astype(
        np.uint8
    )
    spviz.tap(
        burst_mask,
        "Spectral activity mask",
        axes=["frame", "channel center"],
        coordinates=map_coordinates,
        operation="per-frame robust noise threshold",
        inputs=integrated_power,
        units="binary",
        vmin=0,
        vmax=1,
    )
    occupancy = burst_mask.mean(axis=0).astype(np.float32)
    spviz.tap(
        occupancy,
        "Channel occupancy",
        axes=["channel center"],
        coordinates={
            "channel center": {"values": channel_frequencies_khz, "units": "kHz"}
        },
        operation="fraction of active frames",
        inputs=burst_mask,
        units="fraction",
        vmin=0,
        vmax=1,
    )
    recorder.session.metadata["active_time_frequency_cells"] = int(burst_mask.sum())
    recorder.close()
    return path


def _analytic_magnitude(value: np.ndarray, axis: int = -1) -> np.ndarray:
    spectrum = np.fft.fft(value, axis=axis)
    length = value.shape[axis]
    multiplier = np.zeros(length)
    multiplier[0] = 1
    multiplier[1 : (length + 1) // 2] = 2
    if length % 2 == 0:
        multiplier[length // 2] = 1
    shape = [1] * value.ndim
    shape[axis] = length
    return np.abs(np.fft.ifft(spectrum * multiplier.reshape(shape), axis=axis))


def generate_ultrasound(path: Path) -> Path:
    """Generate a plane-wave ultrasound delay-and-sum B-mode run."""
    seed = 83
    rng = np.random.default_rng(seed)
    sound_speed_mps = 1_540.0
    sample_rate_hz = 20_000_000.0
    center_frequency_hz = 4_000_000.0
    channels = 24
    samples = 1_152
    scanlines = 56
    depth_samples = 320
    element_positions_m = (np.arange(channels) - (channels - 1) / 2) * 0.00030
    sample_time_s = np.arange(samples) / sample_rate_hz

    strong_reflectors = (
        {"x_mm": -3.0, "depth_mm": 17.0, "amplitude": 1.0},
        {"x_mm": 2.5, "depth_mm": 28.0, "amplitude": 0.78},
        {"x_mm": 0.0, "depth_mm": 38.0, "amplitude": 0.62},
    )
    scatterers = [
        (item["x_mm"] / 1e3, item["depth_mm"] / 1e3, item["amplitude"])
        for item in strong_reflectors
    ]
    for _ in range(90):
        scatterers.append(
            (
                float(rng.uniform(-8.0, 8.0) / 1e3),
                float(rng.uniform(7.0, 42.0) / 1e3),
                float(rng.normal(0.0, 0.035)),
            )
        )
    channel_rf = np.zeros((channels, samples), dtype=np.float64)
    pulse_half_width = 14
    pulse_sigma_samples = 4.2
    for scatterer_x, scatterer_depth, amplitude in scatterers:
        transmit_time = scatterer_depth / sound_speed_mps
        for channel, element_x in enumerate(element_positions_m):
            receive_time = (
                np.hypot(scatterer_x - element_x, scatterer_depth) / sound_speed_mps
            )
            center_sample = (transmit_time + receive_time) * sample_rate_hz
            first = max(0, int(np.floor(center_sample)) - pulse_half_width)
            last = min(samples, int(np.floor(center_sample)) + pulse_half_width + 1)
            indices = np.arange(first, last)
            relative = indices - center_sample
            pulse = np.exp(-0.5 * (relative / pulse_sigma_samples) ** 2) * np.cos(
                2 * np.pi * center_frequency_hz / sample_rate_hz * relative
            )
            channel_rf[channel, indices] += amplitude * pulse
    channel_rf += 0.009 * rng.normal(size=channel_rf.shape)
    channel_rf = channel_rf.astype(np.float32)

    recorder = spviz.init(
        path,
        name="Plane-wave ultrasound B-mode",
        metadata={
            "seed": seed,
            "sound_speed_mps": sound_speed_mps,
            "sample_rate_hz": sample_rate_hz,
            "center_frequency_hz": center_frequency_hz,
            "strong_reflectors": list(strong_reflectors),
        },
    )
    raw_coordinates = {
        "receive element": {"values": element_positions_m * 1e3, "units": "mm"},
        "echo time": {"values": sample_time_s * 1e6, "units": "µs"},
    }
    low, high = _trace_limits(channel_rf, 0.2, 99.8)
    spviz.tap(
        channel_rf,
        "Receive-channel RF",
        axes=["receive element", "echo time"],
        coordinates=raw_coordinates,
        operation="plane-wave transmit + pulse-echo propagation + receiver noise",
        units="normalized voltage",
        vmin=low,
        vmax=high,
    )

    scanline_positions_m = np.linspace(-8.0, 8.0, scanlines) / 1e3
    depths_m = np.linspace(5.0, 43.0, depth_samples) / 1e3
    apodization = np.hanning(channels)
    apodization /= apodization.sum()
    focused_contributions = np.empty(
        (scanlines, channels, depth_samples), dtype=np.float32
    )
    source_indices = np.arange(samples)
    for scanline, scanline_x in enumerate(scanline_positions_m):
        transmit_time = depths_m / sound_speed_mps
        for channel, element_x in enumerate(element_positions_m):
            receive_time = np.hypot(scanline_x - element_x, depths_m) / sound_speed_mps
            sample_positions = (transmit_time + receive_time) * sample_rate_hz
            focused_contributions[scanline, channel] = apodization[channel] * np.interp(
                sample_positions,
                source_indices,
                channel_rf[channel],
                left=0.0,
                right=0.0,
            )
    volume_coordinates = {
        "scanline": {"values": scanline_positions_m * 1e3, "units": "mm"},
        "receive element": {"values": element_positions_m * 1e3, "units": "mm"},
        "depth": {"values": depths_m * 1e3, "units": "mm"},
    }
    low, high = _trace_limits(focused_contributions, 0.2, 99.8)
    spviz.tap(
        focused_contributions,
        "Focused channel-contribution volume",
        axes=["scanline", "receive element", "depth"],
        coordinates=volume_coordinates,
        operation="fractional receive-delay interpolation + Hann apodization",
        inputs=channel_rf,
        units="weighted RF amplitude",
        vmin=low,
        vmax=high,
        metadata={
            "description": "One dynamically focused RF contribution per receive element."
        },
    )
    beamformed_rf = focused_contributions.sum(axis=1)
    image_coordinates = {
        "scanline": {"values": scanline_positions_m * 1e3, "units": "mm"},
        "depth": {"values": depths_m * 1e3, "units": "mm"},
    }
    low, high = _trace_limits(beamformed_rf, 0.2, 99.8)
    spviz.tap(
        beamformed_rf,
        "Delay-and-sum RF image",
        axes=["scanline", "depth"],
        coordinates=image_coordinates,
        operation="coherent sum across receive aperture",
        inputs=focused_contributions,
        units="normalized voltage",
        vmin=low,
        vmax=high,
    )
    envelope = _analytic_magnitude(beamformed_rf, axis=1).astype(np.float32)
    envelope /= max(float(envelope.max()), np.finfo(np.float32).eps)
    low, high = _magnitude_limits(envelope, 45, 99.9)
    spviz.tap(
        envelope,
        "B-mode envelope",
        axes=["scanline", "depth"],
        coordinates=image_coordinates,
        operation="analytic-signal envelope detection",
        inputs=beamformed_rf,
        units="normalized amplitude",
        scale="log",
        vmin=max(low, 1e-6),
        vmax=max(high, float(envelope.max())),
    )
    chosen_x_mm = strong_reflectors[0]["x_mm"]
    chosen_line = int(np.argmin(np.abs(scanline_positions_m * 1e3 - chosen_x_mm)))
    a_line = envelope[chosen_line]
    low, _ = _magnitude_limits(a_line, 20, 99.5)
    spviz.tap(
        a_line,
        "Envelope A-line",
        axes=["depth"],
        coordinates={"depth": {"values": depths_m * 1e3, "units": "mm"}},
        operation=f"slice nearest x={chosen_x_mm:.1f} mm",
        inputs=envelope,
        units="normalized amplitude",
        scale="log",
        vmin=max(low, 1e-6),
        vmax=float(a_line.max()),
    )
    reflector_mask = (envelope > 0.32).astype(np.uint8)
    spviz.tap(
        reflector_mask,
        "Strong-reflector mask",
        axes=["scanline", "depth"],
        coordinates=image_coordinates,
        operation="envelope threshold at −9.9 dB",
        inputs=envelope,
        units="binary",
        vmin=0,
        vmax=1,
    )
    brightest_line, brightest_depth = np.unravel_index(
        int(np.argmax(envelope)), envelope.shape
    )
    recorder.session.metadata.update(
        {
            "brightest_location_x_mm": float(
                scanline_positions_m[brightest_line] * 1e3
            ),
            "brightest_location_depth_mm": float(depths_m[brightest_depth] * 1e3),
            "strong_reflector_cells": int(reflector_mask.sum()),
        }
    )
    recorder.close()
    return path


GENERATORS: dict[str, Callable[[Path], Path]] = {
    "gnss": generate_gnss,
    "ct": generate_ct,
    "channelizer": generate_channelizer,
    "ultrasound": generate_ultrasound,
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate advanced deterministic spviz examples"
    )
    parser.add_argument("example", choices=[*GENERATORS, "all"])
    parser.add_argument("--output", type=Path, default=Path("runs"))
    arguments = parser.parse_args()
    if arguments.example == "all":
        for example_name, generator in GENERATORS.items():
            print(generator(arguments.output / example_name))
    else:
        print(GENERATORS[arguments.example](arguments.output))
