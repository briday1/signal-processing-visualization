"""Narrowband far-field interferometry: voltages → visibilities → dirty image.

Independent noise-like sources are mutually incoherent. Baseline coordinates
are measured in wavelengths; this is a one-dimensional direction-cosine image.
No range information or deconvolution is implied by this tone-band example.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import spviz


def generate(output: Path, seed: int = 23) -> Path:
    rng = np.random.default_rng(seed)
    positions = np.array([0, 0.5, 1.5, 3, 5, 7.5, 10.5, 14.0])
    angles = np.array([-18.0, 26.0])
    powers = np.array([1.0, 0.55])
    blocks, samples = 20, 512
    steering = np.exp(2j * np.pi * positions[:, None] * np.sin(np.deg2rad(angles)))
    sources = (
        rng.normal(size=(2, blocks, samples))
        + 1j * rng.normal(size=(2, blocks, samples))
    ) * np.sqrt(powers[:, None, None] / 2)
    voltage = np.einsum("rs,sbt->rbt", steering, sources)
    voltage += 0.15 * (
        rng.normal(size=voltage.shape) + 1j * rng.normal(size=voltage.shape)
    )
    views = {
        "Amplitude": {"representation": "magnitude"},
        "Phase": {"representation": "phase"},
    }
    with spviz.Session(
        output,
        name="Narrowband aperture interferometry",
        metadata={
            "seed": seed,
            "source_angles_deg": angles,
            "source_powers": powers,
            "description": "1D far-field dirty image of two mutually incoherent sources; no range recovery or deconvolution.",
        },
    ) as run:
        raw = run.capture(
            "Receiver voltages",
            voltage,
            axes=["receiver", "integration", "sample"],
            coordinates={
                "receiver": {"values": positions, "units": "wavelengths"},
                "integration": np.arange(blocks),
                "sample": np.arange(samples),
            },
            units="voltage",
            views=views,
            primary_view="Phase",
        )
        # V_ij = <x_i conjugate(x_j)>; positive baseline b = r_i - r_j.
        i, j = np.tril_indices(len(positions), k=-1)
        baseline = positions[i] - positions[j]
        order = np.argsort(baseline)
        i, j, baseline = i[order], j[order], baseline[order]
        cross = voltage[i] * voltage[j].conj()
        cross_id = run.capture(
            "Baseline cross-products",
            cross,
            axes=["baseline", "integration", "sample"],
            coordinates={
                "baseline": {"values": baseline, "units": "wavelengths"},
                "integration": np.arange(blocks),
                "sample": np.arange(samples),
            },
            upstream=raw,
            operation="x_i × conjugate(x_j)",
            views=views,
            primary_view="Phase",
        )
        visibility = cross.mean(axis=-1)
        vis_id = run.capture(
            "Integrated visibilities",
            visibility,
            axes=["baseline", "integration"],
            coordinates={
                "baseline": {"values": baseline, "units": "wavelengths"},
                "integration": np.arange(blocks),
            },
            upstream=cross_id,
            operation="average samples",
            views=views,
            primary_view="Phase",
        )
        scan = np.linspace(-60, 60, 601)
        kernel = np.exp(-2j * np.pi * baseline[:, None] * np.sin(np.deg2rad(scan)))
        # Include conjugate baselines: their sum is twice the real part.
        dirty = np.real(visibility.T @ kernel) / len(baseline)
        run.capture(
            "Dirty angular image",
            dirty,
            axes=["integration", "look angle"],
            coordinates={
                "integration": np.arange(blocks),
                "look angle": {"values": scan, "units": "deg"},
            },
            upstream=vis_id,
            operation="conjugate-baseline synthesis",
            units="relative brightness",
        )
        psf = np.real(kernel.mean(axis=0))
        run.capture(
            "Broadside point-spread function",
            psf,
            axes=["look angle"],
            coordinates={"look angle": {"values": scan, "units": "deg"}},
            operation="unit-visibility aperture response",
            units="normalized response",
        )
        run.metadata["brightest_angle_deg"] = float(scan[np.argmax(dirty.mean(axis=0))])
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/interferometry-demo"))
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()
    print(generate(args.output, args.seed))
