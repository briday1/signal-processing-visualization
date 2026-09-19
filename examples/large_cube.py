"""A 16 × 256 × 1024 complex radar cube (4,194,304 samples per tap)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import spviz


def generate(output: Path) -> Path:
    receiver = np.arange(16, dtype=np.float32)[:, None, None]
    pulse = np.arange(256, dtype=np.float32)[None, :, None]
    sample = np.arange(1024, dtype=np.float32)[None, None, :]
    rng = np.random.default_rng(71)
    cube = np.zeros((16, 256, 1024), dtype=np.complex64)
    for range_bin, doppler, bearing, amplitude in [
        (120, 19, 0.12, 1),
        (390, -37, -0.21, 0.65),
        (780, 63, 0.32, 0.4),
    ]:
        phase = (
            2
            * np.pi
            * (sample * range_bin / 1024 + pulse * doppler / 256 + receiver * bearing)
        )
        cube += amplitude * np.exp(1j * phase).astype(np.complex64)
    cube += 0.12 * (
        rng.standard_normal(cube.shape, dtype=np.float32)
        + 1j * rng.standard_normal(cube.shape, dtype=np.float32)
    )
    views = {
        "Amplitude": {"representation": "magnitude"},
        "Phase": {"representation": "phase"},
        "Real": {"representation": "real"},
    }
    with spviz.Session(
        output, name="Large radar cube · 4.2 million samples", metadata={"seed": 71}
    ) as run:
        raw = run.capture(
            "Receiver I/Q",
            cube,
            axes=["receiver", "pulse", "sample"],
            views=views,
            overview_aspect="fit",
        )
        spectrum = np.fft.fftshift(
            np.fft.fft2(
                cube * np.hanning(256)[None, :, None] * np.hanning(1024)[None, None, :],
                axes=(1, 2),
            ),
            axes=(1, 2),
        ).astype(np.complex64)
        run.capture(
            "Range–Doppler cube",
            spectrum,
            axes=["receiver", "Doppler", "range"],
            upstream=raw,
            operation="window + 2D FFT",
            views={**views, "Amplitude": {"representation": "magnitude", "scale": "log"}},
            overview_aspect="fit",
        )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/large-cube"))
    generate(parser.parse_args().output)
