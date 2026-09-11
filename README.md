# spviz

`spviz` is a TensorBoard-style observer for intermediate signal-processing data products. Your application continues to own execution, scheduling, and data flow. `spviz` only taps values that the application already produced, records their semantic axes and lineage, and serves an interactive visualization afterward.

**[Open the live synthetic radar demo](https://briday1.github.io/signal-processing-visualization/)**

GitHub Actions regenerates that example from `examples/radar.py` and deploys it to Pages on every push to `main`. You can create the same serverless bundle yourself with `spviz export-static RUN_DIR OUTPUT_DIR`.

## Install and run the radar example

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python examples/radar.py
spviz serve runs/radar-demo
```

Choose a different port with either `--port` or `-p`:

```bash
spviz serve runs/radar-demo --port 9000
```

Open <http://127.0.0.1:8765>. Click any product without losing the pipeline overview, permute axes, scrub or animate layers, isolate a layer, adjust the opacity of other layers, rotate the volume, and inspect individual values.

The viewer includes dark and light interface themes plus Spviz, Viridis, Plasma, Inferno, Magma, and Cividis color maps. The latter five use the familiar Matplotlib palette endpoints; values at or below the selected minimum remain transparent so the chosen page theme forms the visualization's low-end background.

The inspector can export the selected axis-labeled layer as PNG, the current transparent stack as PNG, an animated GIF sweep through the selected depth axis, or the complete processing chain as PNG. Exports preserve the active axis permutation, coordinates, units, color limits, log mode, transparency, and selected layer where applicable.

The pixel-density control trades fidelity for interaction speed using an explicit samples-per-displayed-axis count. Its maximum is the selected plane's largest native dimension, which requests the full plane without downsampling. The pipeline overview remains fixed at a lightweight 64 samples per axis.

## Observe your existing pipeline

```python
import numpy as np
import spviz

spviz.init("runs/my-run", name="My receiver")

# These functions belong to your application. spviz does not call them.
iq = read_receiver()
spviz.tap(iq, "Raw I/Q", axes=["channel", "pulse", "sample"], units="volts")

beamformed = beamform(iq)
spviz.tap(
    beamformed,
    "Beamformed I/Q",
    filename="beamformed_iq.npy",
    axes=["beam", "pulse", "sample"],
    scale="log",
    operation="beamform",
    inputs=iq,
)

range_doppler = process_range_doppler(beamformed)
spviz.tap(
    range_doppler,
    "Range–Doppler",
    axes=["beam", "doppler", "range"],
    operation="range + Doppler FFT",
    inputs=beamformed,
)

spviz.close()
```

`axes` names every source dimension. Arrays with one to three dimensions use all of them by default. For higher-dimensional products, explicitly choose the three spatial dimensions while preserving the full source shape:

```python
spviz.tap(
    data,
    "Range–Doppler history",
    axes=["frame", "beam", "doppler", "range"],
    view_axes=["beam", "doppler", "range"],
    coordinates={
        "frame": timestamps,
        "beam": {"values": look_angles, "units": "deg"},
        "doppler": {"values": velocities, "units": "m/s"},
        "range": {"values": ranges, "units": "m"},
    },
)
```

Coordinates may be numeric, categorical, or temporal. They are stored as separate NumPy arrays and loaded only when needed. The inspector presents permutations using axis names—not anonymous dimension numbers—and displays coordinate ranges, physical layer values, units, and coordinates for selected cells. Non-view dimensions remain part of the captured product and are indexed at zero by the current viewer.

`tap()` returns the exact object it receives, so it can also be inserted inline without changing the chain:

```python
beamformed = spviz.tap(beamform(iq), "Beamformed", inputs=iq)
```

`filename=` controls the `.npy` filename inside the run's `arrays/` directory. It is intentionally a filename rather than an arbitrary path, keeping runs self-contained and portable. When omitted, `spviz` derives a safe filename from the display name and adds a suffix for repeated names.

`scale=` sets the product's default visualization scale to `"linear"` (the default) or `"log"`. It initializes the inspector and is also honored by the full-chain overview. Users can still toggle the selected product interactively.

For code where wrapping a function is convenient, optional instrumentation observes its return value while leaving invocation and scheduling with the original application:

```python
spviz.init("runs/my-run")

@spviz.instrument(name="Filtered I/Q", axes=["channel", "sample"])
def filter_bank(iq):
    return existing_filter_implementation(iq)
```

Observed runs are portable directories containing `manifest.json` and standard NumPy `.npy` files. The browser requests a resolution-limited visualization volume once, then changes layers locally for responsive interaction without loading the full source array.

## Current scope

- NumPy arrays with arbitrary dimensionality
- Directed product lineage and operation labels
- Local, dependency-light HTTP server
- Transparent stacked-slice volume rendering
- Axis permutation, layer playback/isolation, opacity, and value inspection
- Deterministic synthetic phased-array radar example with clutter, receiver mismatch, thermal noise, windowed FFTs, an explicit cell-average noise estimate, and binary CA-CFAR detections

This is an initial foundation. Live streaming, framework adapters, timeline comparison, GPU-side capture, and richer plots are intentionally left for later versions.
