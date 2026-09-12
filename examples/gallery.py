"""Build five deterministic signal-processing examples for GitHub Pages."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import spviz
from radar import generate as generate_radar
from spviz.static import export_static


def limits(value, low=35, high=99.7):
    magnitude = np.abs(value)
    return float(np.percentile(magnitude, low)), float(np.percentile(magnitude, high))


def trace_limits(value, low=.5, high=99.5):
    return float(np.percentile(value, low)), float(np.percentile(value, high))


def generate_audio(path: Path) -> Path:
    rng = np.random.default_rng(11)
    microphones, frames, samples = 6, 28, 128
    t = np.arange(samples) / 16_000
    frame_time = np.arange(frames) * 8.0
    angles = np.linspace(-60, 60, 13)
    source_angle = 22.0
    delay = np.arange(microphones) * np.sin(np.deg2rad(source_angle)) * 1.7
    phase = 2 * np.pi * (950 + 18 * np.arange(frames)[:, None]) * t
    source = np.sin(phase)[None, :, :] * (0.35 + 0.65 * np.hanning(frames)[None, :, None])
    raw = np.empty((microphones, frames, samples), np.float32)
    for mic in range(microphones):
        raw[mic] = np.roll(source[0], int(round(delay[mic])), axis=1)
    raw += 0.24 * rng.normal(size=raw.shape)
    rec = spviz.init(path, name="Microphone-array tone tracker", metadata={"seed": 11})
    v0, v1 = limits(raw, 10, 99.8)
    spviz.tap(raw, "Microphone waveforms", axes=["microphone", "frame", "sample"], coordinates={"microphone": np.arange(microphones), "frame": {"values": frame_time, "units": "ms"}, "sample": {"values": t * 1e3, "units": "ms"}}, units="amplitude", vmin=v0, vmax=v1)
    steering = np.array([[int(round(m * np.sin(np.deg2rad(a)) * 1.7)) for m in range(microphones)] for a in angles])
    beams = np.stack([np.mean([np.roll(raw[m], -steering[i, m], axis=1) for m in range(microphones)], axis=0) for i in range(len(angles))])
    v0, v1 = limits(beams)
    spviz.tap(beams, "Steered audio beams", axes=["look angle", "frame", "sample"], coordinates={"look angle": {"values": angles, "units": "deg"}, "frame": {"values": frame_time, "units": "ms"}, "sample": {"values": t * 1e3, "units": "ms"}}, operation="delay-and-sum", inputs=raw, vmin=v0, vmax=v1)
    spectrum = np.abs(np.fft.rfft(beams * np.hanning(samples), axis=2)).astype(np.float32) ** 2
    frequencies = np.fft.rfftfreq(samples, 1 / 16_000)
    v0, v1 = limits(spectrum, 55, 99.9)
    spviz.tap(spectrum, "Short-time spectrum", axes=["look angle", "frame", "frequency"], coordinates={"look angle": {"values": angles, "units": "deg"}, "frame": {"values": frame_time, "units": "ms"}, "frequency": {"values": frequencies, "units": "Hz"}}, operation="Hann + real FFT", inputs=beams, units="power", scale="log", vmin=v0, vmax=v1)
    noise = np.median(spectrum, axis=1, keepdims=True) * np.ones_like(spectrum)
    v0, v1 = limits(noise, 30, 99.5)
    spviz.tap(noise, "Spectral noise floor", axes=["look angle", "frame", "frequency"], operation="temporal median", inputs=spectrum, units="power", scale="log", vmin=v0, vmax=v1)
    mask = (spectrum > noise * 14).astype(np.uint8)
    spviz.tap(mask, "Tracked tone mask", axes=["look angle", "frame", "frequency"], operation="SNR threshold", inputs=[spectrum, noise], units="binary", vmin=0, vmax=1)
    rec.close()
    return path


def generate_comms(path: Path) -> Path:
    rng = np.random.default_rng(19)
    antennas, bursts, symbols, sps = 4, 20, 32, 4
    bits = rng.integers(0, 4, (bursts, symbols))
    qpsk = np.exp(1j * (np.pi / 4 + bits * np.pi / 2))
    up = np.repeat(qpsk, sps, axis=1)
    n = np.arange(symbols * sps)
    raw = np.stack([up * np.exp(1j * (0.035 * n + .22 * antenna)) for antenna in range(antennas)])
    raw += 0.18 * (rng.normal(size=raw.shape) + 1j * rng.normal(size=raw.shape))
    raw[:, [6, 13, 17]] += 0.7 * (rng.normal(size=(antennas, 3, symbols * sps)) + 1j * rng.normal(size=(antennas, 3, symbols * sps)))
    raw[:, [6, 13, 17]] *= np.exp(1j * 1.05)  # unresolved burst phase slips
    rec = spviz.init(path, name="QPSK burst receiver", metadata={"seed": 19})
    v0, v1 = limits(raw, 5, 99.8)
    spviz.tap(raw, "Antenna I/Q frames", axes=["antenna", "burst", "sample"], view_axes=["burst", "antenna", "sample"], units="normalized voltage", vmin=v0, vmax=v1)
    corrected = raw * np.exp(-1j * 0.035 * n)[None, None, :]
    v0, v1 = limits(corrected, 5, 99.8)
    spviz.tap(corrected, "Carrier-corrected frames", axes=["antenna", "burst", "sample"], view_axes=["burst", "antenna", "sample"], operation="frequency derotation", inputs=raw, vmin=v0, vmax=v1)
    kernel = np.ones(sps) / sps
    matched = np.apply_along_axis(lambda x: np.convolve(x, kernel, mode="same"), 2, corrected)
    v0, v1 = limits(matched, 20, 99.5)
    spviz.tap(matched, "Matched-filter frames", axes=["antenna", "burst", "sample"], view_axes=["burst", "antenna", "sample"], operation="rectangular matched filter", inputs=corrected, vmin=v0, vmax=v1)
    antenna_phase = np.exp(-1j * .22 * np.arange(antennas))[:, None, None]
    sampled = np.mean(matched * antenna_phase, axis=0)[:, 2::sps][:, :symbols]
    edges = np.linspace(-1.8, 1.8, 49)
    centers = (edges[:-1] + edges[1:]) / 2
    constellation = np.stack([np.histogram2d(frame.imag, frame.real, bins=(edges, edges))[0] for frame in sampled]).astype(np.float32)
    spviz.tap(constellation, "Constellation density", axes=["burst", "quadrature", "in-phase"], coordinates={"burst": np.arange(bursts), "quadrature": centers, "in-phase": centers}, operation="symbol sampling + I/Q histogram", inputs=matched, scale="log", vmin=0.35, vmax=float(max(1, constellation.max())))
    references = np.exp(1j * (np.pi / 4 + np.arange(4) * np.pi / 2))
    decisions = np.argmin(np.abs(sampled[:, :, None] - references[None, None, :]), axis=2)
    symbol_errors = decisions != bits
    error_density = np.stack([np.histogram2d(frame.imag[symbol_errors[index]], frame.real[symbol_errors[index]], bins=(edges, edges))[0] for index, frame in enumerate(sampled)]).astype(np.float32)
    spviz.tap(error_density, "Decision-error density", axes=["burst", "quadrature", "in-phase"], coordinates={"burst": np.arange(bursts), "quadrature": centers, "in-phase": centers}, operation="hard decisions vs transmitted symbols", inputs=constellation, units="errors/bin", vmin=0, vmax=float(max(1, error_density.max())), metadata={"total_symbol_errors": int(symbol_errors.sum())})
    rec.close()
    return path


def generate_seismic(path: Path) -> Path:
    rng = np.random.default_rng(23)
    stations, windows, samples = 9, 24, 128
    x = np.arange(samples)
    raw = 0.22 * rng.normal(size=(stations, windows, samples))
    for station in range(stations):
        arrival = 35 + station * 3
        pulse = np.exp(-((x - arrival) / 9) ** 2) * np.sin(.42 * (x - arrival))
        raw[station, 8:17] += np.hanning(9)[:, None] * pulse
    rec = spviz.init(path, name="Seismic array event detector", metadata={"seed": 23})
    v0, v1 = limits(raw, 20, 99.8)
    spviz.tap(raw, "Station traces", axes=["station", "window", "sample"], units="velocity", vmin=v0, vmax=v1)
    filtered = np.apply_along_axis(lambda a: np.convolve(a, np.ones(7) / 7, mode="same"), 2, raw)
    v0, v1 = limits(filtered, 25, 99.7)
    spviz.tap(filtered, "Band-limited traces", axes=["station", "window", "sample"], operation="moving-average low-pass", inputs=raw, vmin=v0, vmax=v1)
    spectrum = np.abs(np.fft.rfft(filtered * np.hanning(samples), axis=2)) ** 2
    v0, v1 = limits(spectrum, 55, 99.8)
    spviz.tap(spectrum, "Window spectra", axes=["station", "window", "frequency bin"], operation="window + FFT", inputs=filtered, units="power", scale="log", vmin=max(v0, 1e-8), vmax=v1)
    energy = np.cumsum(filtered ** 2, axis=2)
    v0, v1 = limits(energy, 55, 99.5)
    spviz.tap(energy, "Cumulative event energy", axes=["station", "window", "sample"], operation="square + integrate", inputs=filtered, units="energy", scale="log", vmin=max(v0, 1e-8), vmax=v1)
    trigger = (energy > np.percentile(energy, 94)).astype(np.uint8)
    spviz.tap(trigger, "Event trigger mask", axes=["station", "window", "sample"], operation="STA/LTA-style threshold", inputs=energy, units="binary", vmin=0, vmax=1)
    rec.close()
    return path


def generate_ecg(path: Path) -> Path:
    rng = np.random.default_rng(31)
    leads, beats, samples = 6, 24, 128
    x = np.arange(samples)
    template = 0.15 * np.exp(-((x - 28) / 7) ** 2) - 0.25 * np.exp(-((x - 57) / 3) ** 2) + np.exp(-((x - 62) / 2.2) ** 2) - 0.32 * np.exp(-((x - 68) / 4) ** 2) + 0.28 * np.exp(-((x - 94) / 11) ** 2)
    raw = np.stack([(1 - .08 * lead) * template[None, :] + .08 * np.sin(x / 35 + lead) + .055 * rng.normal(size=(beats, samples)) for lead in range(leads)])
    rec = spviz.init(path, name="Multi-lead ECG conditioning", metadata={"seed": 31})
    v0, v1 = limits(raw, 12, 99.7)
    spviz.tap(raw, "Raw ECG leads", axes=["lead", "beat", "sample"], units="mV", vmin=v0, vmax=v1)
    baseline = np.apply_along_axis(lambda a: np.convolve(a, np.ones(21) / 21, mode="same"), 2, raw)
    corrected = raw - baseline
    v0, v1 = limits(corrected, 20, 99.7)
    spviz.tap(corrected, "Baseline-corrected ECG", axes=["lead", "beat", "sample"], operation="baseline subtraction", inputs=raw, units="mV", vmin=v0, vmax=v1)
    gradient = np.gradient(corrected, axis=2)
    v0, v1 = limits(gradient, 35, 99.7)
    spviz.tap(gradient, "QRS slope", axes=["lead", "beat", "sample"], operation="temporal derivative", inputs=corrected, vmin=v0, vmax=v1)
    envelope = np.apply_along_axis(lambda a: np.convolve(a * a, np.ones(9) / 9, mode="same"), 2, gradient)
    v0, v1 = limits(envelope, 55, 99.8)
    spviz.tap(envelope, "Integrated QRS energy", axes=["lead", "beat", "sample"], operation="square + integrate", inputs=gradient, units="energy", scale="log", vmin=max(v0, 1e-9), vmax=v1)
    peaks = (envelope > np.percentile(envelope, 94)).astype(np.uint8)
    spviz.tap(peaks, "R-peak candidate mask", axes=["lead", "beat", "sample"], operation="adaptive threshold", inputs=envelope, units="binary", vmin=0, vmax=1)
    rec.close()
    return path


def generate_pulse_compression(path: Path) -> Path:
    rng = np.random.default_rng(41)
    samples = 512
    time_us = np.arange(samples) * 0.08
    chirp_samples = 96
    chirp_time = np.linspace(-1, 1, chirp_samples)
    transmit = np.exp(1j * np.pi * 18 * chirp_time**2) * np.hanning(chirp_samples)
    received = 0.10 * (rng.normal(size=samples) + 1j * rng.normal(size=samples))
    for delay, amplitude, phase in [(88, 1.0, .2), (241, .62, -1.1), (367, .35, 1.8)]:
        received[delay : delay + chirp_samples] += amplitude * transmit * np.exp(1j * phase)
    rec = spviz.init(path, name="LFM pulse-compression receiver", metadata={"seed": 41})
    spviz.tap(transmit, "Transmit LFM chirp", axes=["fast time"], coordinates={"fast time": {"values": time_us[:chirp_samples], "units": "µs"}}, units="amplitude", vmin=.03, vmax=1)
    v0, v1 = limits(received, 35, 99.8)
    spviz.tap(received, "Received complex echo", axes=["fast time"], coordinates={"fast time": {"values": time_us, "units": "µs"}}, operation="channel + thermal noise", inputs=transmit, units="amplitude", vmin=v0, vmax=v1)
    compressed = np.abs(np.convolve(received, np.conj(transmit[::-1]), mode="same")) ** 2
    v0, v1 = limits(compressed, 45, 99.9)
    spviz.tap(compressed, "Compressed range profile", axes=["range bin"], coordinates={"range bin": {"values": np.arange(samples) * 12.0, "units": "m"}}, operation="matched filter", inputs=received, units="power", scale="log", vmin=max(v0, 1e-8), vmax=v1)
    training = np.ones(33); training[13:20] = 0; training /= training.sum()
    noise = np.convolve(compressed, training, mode="same")
    v0, v1 = limits(noise, 35, 99.5)
    spviz.tap(noise, "CA-CFAR noise estimate", axes=["range bin"], coordinates={"range bin": {"values": np.arange(samples) * 12.0, "units": "m"}}, operation="training-cell average", inputs=compressed, units="power", scale="log", vmin=max(v0, 1e-8), vmax=v1)
    detections = (compressed > noise * 7).astype(np.uint8)
    spviz.tap(detections, "Range detections", axes=["range bin"], operation="CFAR threshold", inputs=[compressed, noise], units="binary", vmin=0, vmax=1)
    rec.close()
    return path


def generate_equalizer(path: Path) -> Path:
    rng = np.random.default_rng(47)
    sample_rate, samples = 16_000, 1024
    time = np.arange(samples) / sample_rate
    raw = .75 * np.sin(2*np.pi*440*time) + .32 * np.sin(2*np.pi*120*time) + .18 * np.sin(2*np.pi*3100*time) + .10 * rng.normal(size=samples)
    taps = 81
    centered = np.arange(taps) - (taps - 1) / 2
    cutoff = 1800 / sample_rate
    lowpass = 2 * cutoff * np.sinc(2 * cutoff * centered) * np.hamming(taps)
    lowpass /= lowpass.sum()
    rec = spviz.init(path, name="Audio FIR equalizer", metadata={"seed": 47, "sample_rate_hz": sample_rate})
    v0, v1 = trace_limits(raw)
    spviz.tap(raw, "Input audio waveform", axes=["time"], coordinates={"time": {"values": time * 1e3, "units": "ms"}}, units="amplitude", vmin=v0, vmax=v1)
    spviz.tap(lowpass, "Windowed-sinc FIR taps", axes=["tap"], operation="low-pass design", inputs=raw, vmin=float(lowpass.min()), vmax=float(lowpass.max()))
    filtered = np.convolve(raw, lowpass, mode="same")
    v0, v1 = trace_limits(filtered)
    spviz.tap(filtered, "Equalized waveform", axes=["time"], coordinates={"time": {"values": time * 1e3, "units": "ms"}}, operation="FIR convolution", inputs=[raw, lowpass], units="amplitude", vmin=v0, vmax=v1)
    frequencies = np.fft.rfftfreq(samples, 1/sample_rate)
    spectrum = np.abs(np.fft.rfft(raw * np.hanning(samples))) ** 2
    filtered_spectrum = np.abs(np.fft.rfft(filtered * np.hanning(samples))) ** 2
    v0, v1 = limits(spectrum, 55, 99.9)
    spviz.tap(spectrum, "Input power spectrum", axes=["frequency"], coordinates={"frequency": {"values": frequencies, "units": "Hz"}}, operation="Hann + FFT", inputs=raw, units="power", scale="log", vmin=max(v0, 1e-9), vmax=v1)
    v0, v1 = limits(filtered_spectrum, 55, 99.9)
    spviz.tap(filtered_spectrum, "Equalized power spectrum", axes=["frequency"], coordinates={"frequency": {"values": frequencies, "units": "Hz"}}, operation="Hann + FFT", inputs=filtered, units="power", scale="log", vmin=max(v0, 1e-9), vmax=v1)
    rec.close()
    return path


def analytic_envelope(signal: np.ndarray) -> np.ndarray:
    spectrum = np.fft.fft(signal)
    multiplier = np.zeros(len(signal)); multiplier[0] = 1; multiplier[1:(len(signal)+1)//2] = 2
    if len(signal) % 2 == 0: multiplier[len(signal)//2] = 1
    return np.abs(np.fft.ifft(spectrum * multiplier))


def generate_bearing(path: Path) -> Path:
    rng = np.random.default_rng(53)
    sample_rate, samples = 12_800, 2048
    time = np.arange(samples) / sample_rate
    shaft_hz, fault_hz = 30.0, 186.0
    vibration = .18*np.sin(2*np.pi*shaft_hz*time) + .07*rng.normal(size=samples)
    for impact_time in np.arange(.008, time[-1], 1/fault_hz):
        start = int(impact_time * sample_rate); tail = np.arange(min(90, samples-start)) / sample_rate
        vibration[start:start+len(tail)] += .7*np.exp(-900*tail)*np.sin(2*np.pi*2600*tail)
    rec = spviz.init(path, name="Rolling-bearing fault detector", metadata={"seed": 53, "shaft_hz": shaft_hz, "fault_hz": fault_hz})
    v0, v1 = trace_limits(vibration)
    spviz.tap(vibration, "Accelerometer waveform", axes=["time"], coordinates={"time": {"values": time*1e3, "units": "ms"}}, units="g", vmin=v0, vmax=v1)
    spectrum = np.fft.rfft(vibration)
    frequency = np.fft.rfftfreq(samples, 1/sample_rate)
    band = ((frequency > 1800) & (frequency < 3600))
    bandpassed = np.fft.irfft(spectrum * band, n=samples)
    v0, v1 = trace_limits(bandpassed)
    spviz.tap(bandpassed, "Resonance-band waveform", axes=["time"], coordinates={"time": {"values": time*1e3, "units": "ms"}}, operation="FFT band-pass", inputs=vibration, units="g", vmin=v0, vmax=v1)
    envelope = analytic_envelope(bandpassed)
    v0, v1 = limits(envelope, 30, 99.8)
    spviz.tap(envelope, "Impact envelope", axes=["time"], coordinates={"time": {"values": time*1e3, "units": "ms"}}, operation="analytic magnitude", inputs=bandpassed, units="g", vmin=v0, vmax=v1)
    envelope_spectrum = np.abs(np.fft.rfft((envelope-envelope.mean()) * np.hanning(samples))) ** 2
    v0, v1 = limits(envelope_spectrum, 60, 99.9)
    spviz.tap(envelope_spectrum, "Envelope spectrum", axes=["frequency"], coordinates={"frequency": {"values": frequency, "units": "Hz"}}, operation="envelope FFT", inputs=envelope, units="power", scale="log", vmin=max(v0, 1e-10), vmax=v1)
    fault_bins = ((frequency > fault_hz-4) & (frequency < fault_hz+4)) | ((frequency > 2*fault_hz-4) & (frequency < 2*fault_hz+4))
    peaks = (fault_bins & (envelope_spectrum > np.percentile(envelope_spectrum, 90))).astype(np.uint8)
    spviz.tap(peaks, "Bearing-fault harmonics", axes=["frequency"], coordinates={"frequency": {"values": frequency, "units": "Hz"}}, operation="fault-frequency selection", inputs=envelope_spectrum, units="binary", vmin=0, vmax=1)
    rec.close()
    return path


def generate_localization(path: Path) -> Path:
    rng = np.random.default_rng(59)
    sample_rate, samples, microphones = 8_000, 2048, 8
    time = np.arange(samples) / sample_rate
    reference_time = np.arange(256) / sample_rate
    reference = np.sin(2*np.pi*(450*reference_time + 1400*reference_time**2)) * np.hanning(256)
    source = np.zeros(samples); source[620:876] = reference
    true_angle = 28.0
    delays = np.rint(np.arange(microphones) * 2.2 * np.sin(np.deg2rad(true_angle))).astype(int)
    recording = np.stack([np.roll(source, delay) for delay in delays]) + .10*rng.normal(size=(microphones, samples))
    rec = spviz.init(path, name="Acoustic source localization", metadata={"seed": 59, "true_angle_deg": true_angle})
    v0, v1 = trace_limits(reference)
    spviz.tap(reference, "Reference chirp", axes=["time"], coordinates={"time": {"values": reference_time*1e3, "units": "ms"}}, units="amplitude", vmin=v0, vmax=v1)
    v0, v1 = limits(recording, 20, 99.8)
    spviz.tap(recording, "Microphone recording", axes=["microphone", "time"], coordinates={"microphone": np.arange(microphones), "time": {"values": time*1e3, "units": "ms"}}, operation="array capture", inputs=reference, units="amplitude", vmin=v0, vmax=v1)
    angles = np.linspace(-60, 60, 25)
    frame_starts = np.arange(0, samples-128+1, 64)
    beam_spectra = np.empty((len(angles), len(frame_starts), 65), np.float32)
    for angle_index, angle in enumerate(angles):
        steering = np.rint(np.arange(microphones)*2.2*np.sin(np.deg2rad(angle))).astype(int)
        beam = np.mean([np.roll(recording[mic], -steering[mic]) for mic in range(microphones)], axis=0)
        beam_spectra[angle_index] = np.stack([np.abs(np.fft.rfft(beam[start:start+128]*np.hanning(128)))**2 for start in frame_starts])
    frequencies = np.fft.rfftfreq(128, 1/sample_rate)
    v0, v1 = limits(beam_spectra, 60, 99.9)
    spviz.tap(beam_spectra, "Beam time-frequency cube", axes=["look angle", "frame", "frequency"], coordinates={"look angle": {"values": angles, "units": "deg"}, "frame": frame_starts, "frequency": {"values": frequencies, "units": "Hz"}}, operation="steer + STFT", inputs=recording, units="power", scale="log", vmin=max(v0, 1e-9), vmax=v1)
    beam_energy = beam_spectra[:, :, 5:40].sum(axis=2)
    v0, v1 = limits(beam_energy, 45, 99.8)
    spviz.tap(beam_energy, "Broadband beam energy", axes=["look angle", "frame"], coordinates={"look angle": {"values": angles, "units": "deg"}, "frame": frame_starts}, operation="frequency integration", inputs=beam_spectra, units="energy", scale="log", vmin=max(v0, 1e-9), vmax=v1)
    direction_score = beam_energy.max(axis=1)
    spviz.tap(direction_score, "Direction score", axes=["look angle"], coordinates={"look angle": {"values": angles, "units": "deg"}}, operation="peak over time", inputs=beam_energy, units="energy", scale="log", vmin=max(float(np.percentile(direction_score, 10)), 1e-9), vmax=float(direction_score.max()))
    rec.close()
    return path


def generate_ofdm(path: Path) -> Path:
    rng = np.random.default_rng(67)
    frames, symbols, subcarriers = 8, 12, 48
    transmitted_bits = rng.integers(0, 4, (frames, symbols, subcarriers))
    transmitted = np.exp(1j*(np.pi/4 + transmitted_bits*np.pi/2))
    frequency = np.linspace(-1, 1, subcarriers)
    channel = (.8 + .2*np.cos(np.pi*frequency))[None, None, :] * np.exp(1j*(.5*frequency))[None, None, :]
    noise_scale = np.linspace(.06, .24, frames)[:, None, None]
    received_grid = transmitted*channel + noise_scale*(rng.normal(size=transmitted.shape)+1j*rng.normal(size=transmitted.shape))
    captured_iq = np.fft.ifft(received_grid, axis=2).reshape(-1)
    rec = spviz.init(path, name="OFDM receiver quality analysis", metadata={"seed": 67})
    spviz.tap(captured_iq, "Captured OFDM I/Q", axes=["sample"], units="normalized voltage", vmin=float(np.percentile(np.abs(captured_iq), 10)), vmax=float(np.percentile(np.abs(captured_iq), 99.8)))
    v0, v1 = limits(received_grid, 10, 99.8)
    spviz.tap(received_grid, "Received resource-grid cube", axes=["frame", "OFDM symbol", "subcarrier"], operation="symbol framing + FFT", inputs=captured_iq, vmin=v0, vmax=v1)
    equalized = received_grid/channel
    evm = np.abs(equalized-transmitted)
    evm_map = evm.mean(axis=0)
    v0, v1 = limits(evm_map, 5, 99.5)
    spviz.tap(evm_map, "Mean EVM map", axes=["OFDM symbol", "subcarrier"], coordinates={"subcarrier": np.arange(-subcarriers//2, subcarriers//2)}, operation="equalize + frame average", inputs=received_grid, units="error magnitude", vmin=v0, vmax=v1)
    subcarrier_quality = 20*np.log10(1/np.maximum(evm.mean(axis=(0,1)), 1e-6))
    v0, v1 = trace_limits(subcarrier_quality)
    spviz.tap(subcarrier_quality, "Subcarrier quality", axes=["subcarrier"], coordinates={"subcarrier": np.arange(-subcarriers//2, subcarriers//2)}, operation="EVM to quality", inputs=evm_map, units="dB", vmin=v0, vmax=v1)
    references = np.exp(1j*(np.pi/4 + np.arange(4)*np.pi/2))
    decisions = np.argmin(np.abs(equalized[:, :, :, None]-references), axis=3)
    error_mask = (decisions != transmitted_bits).any(axis=0).astype(np.uint8)
    spviz.tap(error_mask, "Decision-error map", axes=["OFDM symbol", "subcarrier"], operation="hard decisions across frames", inputs=[received_grid, evm_map], units="binary", vmin=0, vmax=1)
    rec.close()
    return path


EXAMPLES = [
    ("radar", "Phased-array radar", "Beamforming, range–Doppler processing, cell averaging, and CA-CFAR."),
    ("audio", "Microphone-array audio", "Delay-and-sum steering, spectra, noise estimation, and tone tracking."),
    ("comms", "QPSK receiver", "Carrier correction, matched filtering, symbol errors, and decisions."),
    ("seismic", "Seismic array", "Trace filtering, spectra, energy integration, and event triggering."),
    ("ecg", "Multi-lead ECG", "Baseline removal, QRS enhancement, integration, and peak candidates."),
    ("pulse-compression", "LFM pulse compression", "One-dimensional chirp, echo, matched-filter, CFAR, and detections."),
    ("equalizer", "Audio FIR equalizer", "One-dimensional waveforms, FIR coefficients, and power spectra."),
    ("bearing", "Bearing diagnostics", "One-dimensional vibration, envelope analysis, and fault harmonics."),
    ("localization", "Acoustic localization", "A 1D → 2D → 3D → 2D → 1D array-processing pipeline."),
    ("ofdm", "OFDM receiver", "A 1D capture, 3D resource grid, 2D EVM, and 1D quality trace."),
]


def build_gallery(output: Path) -> Path:
    runs = output.parent / "runs"
    generators = {"radar": generate_radar, "audio": generate_audio, "comms": generate_comms, "seismic": generate_seismic, "ecg": generate_ecg, "pulse-compression": generate_pulse_compression, "equalizer": generate_equalizer, "bearing": generate_bearing, "localization": generate_localization, "ofdm": generate_ofdm}
    for slug, _, _ in EXAMPLES:
        export_static(generators[slug](runs / slug), output / slug)
    cards = "".join(f'<a class="card" href="./{slug}/"><strong>{title}</strong><span>{description}</span><b>Open pipeline →</b></a>' for slug, title, description in EXAMPLES)
    (output / "index.html").write_text(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>spviz examples</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#09101d;color:#e9eef8;font:16px/1.5 system-ui}}main{{max-width:1120px;margin:auto;padding:64px 24px}}h1{{font-size:46px;margin:0}}p{{color:#91a0b8;max-width:700px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin-top:38px}}.card{{min-height:190px;padding:24px;border:1px solid #293752;border-radius:14px;background:#101a2c;color:inherit;text-decoration:none;display:flex;flex-direction:column;transition:.15s}}.card:hover{{transform:translateY(-3px);border-color:#6e83ff}}strong{{font-size:21px}}span{{color:#91a0b8;margin-top:10px}}b{{color:#6e83ff;margin-top:auto}}</style></head><body><main><h1>spviz examples</h1><p>Real deterministic synthetic data flowing through ten different signal-processing pipelines. Choose one to inspect every intermediate product.</p><div class="grid">{cards}</div></main></body></html>''', encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/site"))
    print(build_gallery(parser.parse_args().output))
