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


EXAMPLES = [
    ("radar", "Phased-array radar", "Beamforming, range–Doppler processing, cell averaging, and CA-CFAR."),
    ("audio", "Microphone-array audio", "Delay-and-sum steering, spectra, noise estimation, and tone tracking."),
    ("comms", "QPSK receiver", "Carrier correction, matched filtering, symbol errors, and decisions."),
    ("seismic", "Seismic array", "Trace filtering, spectra, energy integration, and event triggering."),
    ("ecg", "Multi-lead ECG", "Baseline removal, QRS enhancement, integration, and peak candidates."),
]


def build_gallery(output: Path) -> Path:
    runs = output.parent / "runs"
    generators = {"radar": generate_radar, "audio": generate_audio, "comms": generate_comms, "seismic": generate_seismic, "ecg": generate_ecg}
    for slug, _, _ in EXAMPLES:
        export_static(generators[slug](runs / slug), output / slug)
    cards = "".join(f'<a class="card" href="./{slug}/"><strong>{title}</strong><span>{description}</span><b>Open pipeline →</b></a>' for slug, title, description in EXAMPLES)
    (output / "index.html").write_text(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>spviz examples</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#09101d;color:#e9eef8;font:16px/1.5 system-ui}}main{{max-width:1120px;margin:auto;padding:64px 24px}}h1{{font-size:46px;margin:0}}p{{color:#91a0b8;max-width:700px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px;margin-top:38px}}.card{{min-height:190px;padding:24px;border:1px solid #293752;border-radius:14px;background:#101a2c;color:inherit;text-decoration:none;display:flex;flex-direction:column;transition:.15s}}.card:hover{{transform:translateY(-3px);border-color:#6e83ff}}strong{{font-size:21px}}span{{color:#91a0b8;margin-top:10px}}b{{color:#6e83ff;margin-top:auto}}</style></head><body><main><h1>spviz examples</h1><p>Real deterministic synthetic data flowing through five different signal-processing pipelines. Choose one to inspect every intermediate product.</p><div class="grid">{cards}</div></main></body></html>''', encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/site"))
    print(build_gallery(parser.parse_args().output))
