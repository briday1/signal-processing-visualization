# spviz

`spviz` is a TensorBoard-style observer for intermediate signal-processing data products. Your application continues to own execution, scheduling, and data flow. `spviz` only taps values that the application already produced, records their semantic axes and lineage, and serves an interactive visualization afterward.

**[Explore all fourteen live examples](https://briday1.github.io/signal-processing-visualization/)**

## What it looks like

![Transparent Range–Doppler volume with physical axes and interactive controls](https://raw.githubusercontent.com/briday1/signal-processing-visualization/main/docs/images/radar-volume.png)

*A real synthetic phased-array data cube, with the selected plane in focus and the full processing context still visible.*

![GNSS Doppler/code-phase acquisition map](https://raw.githubusercontent.com/briday1/signal-processing-visualization/main/docs/images/gnss-acquisition.png)

![One-dimensional FIR low-pass signal-processing chain](https://raw.githubusercontent.com/briday1/signal-processing-visualization/main/docs/images/fir-lowpass.png)

1. [Phased-array radar](https://briday1.github.io/signal-processing-visualization/radar/) — beamforming, range–Doppler processing, cell averaging, and CA-CFAR.
2. [Microphone-array audio](https://briday1.github.io/signal-processing-visualization/audio/) — delay-and-sum steering, spectra, noise estimation, and tone-candidate detection.
3. [QPSK receiver](https://briday1.github.io/signal-processing-visualization/comms/) — carrier correction, matched filtering, sampled symbol phases, constellation density, and decision errors.
4. [Seismic array](https://briday1.github.io/signal-processing-visualization/seismic/) — trace filtering, spectra, event-energy integration, and triggering.
5. [Multi-lead ECG](https://briday1.github.io/signal-processing-visualization/ecg/) — baseline removal, QRS enhancement, energy integration, and one R-peak detection per aligned beat.
6. [LFM pulse compression](https://briday1.github.io/signal-processing-visualization/pulse-compression/) — 1D chirp, complex echo, matched filtering, CA-CFAR, and detections.
7. [Audio FIR low-pass](https://briday1.github.io/signal-processing-visualization/equalizer/) — 1D waveforms, windowed-sinc low-pass coefficients, convolution, and power spectra.
8. [Rolling-bearing diagnostics](https://briday1.github.io/signal-processing-visualization/bearing/) — 1D vibration, resonance filtering, analytic envelope, and fault harmonics.
9. [Acoustic source localization](https://briday1.github.io/signal-processing-visualization/localization/) — a 1D reference, 2D microphone capture, 3D steered time–frequency cube, 2D beam energy, and 1D direction score.
10. [OFDM receiver quality](https://briday1.github.io/signal-processing-visualization/ofdm/) — a 1D I/Q capture, 3D resource grid, 2D EVM and error maps, and 1D subcarrier quality.
11. [GPS acquisition](https://briday1.github.io/signal-processing-visualization/gnss/) — a real GPS L1 C/A Gold code, noisy multipath I/Q, coherent correlations, acquisition cuts, and detection.
12. [Ultrasound B-mode](https://briday1.github.io/signal-processing-visualization/ultrasound/) — pulse-echo channel RF, fractional-delay focusing, coherent beamforming, envelope detection, and reflector picks.
13. [CT reconstruction](https://briday1.github.io/signal-processing-visualization/ct/) — a modified Shepp–Logan phantom, noisy Radon projections, Ram–Lak filtering, per-angle backprojections, and reconstruction.
14. [Polyphase channelizer](https://briday1.github.io/signal-processing-visualization/channelizer/) — intermittent wideband emitters through a true four-tap PFB/FFT, integration, activity detection, and occupancy.

GitHub Actions regenerates the complete gallery from `examples/radar.py`, `examples/gallery.py`, and `examples/advanced_gallery.py`, then deploys it to Pages on every push to `main`. You can create the same serverless bundle yourself with `spviz export-static RUN_DIR OUTPUT_DIR`.

## Install

```bash
pip install spviz
```

To run the repository's radar example locally:

```bash
git clone https://github.com/briday1/signal-processing-visualization.git
cd signal-processing-visualization
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

Open the URL printed by the server (default: <http://127.0.0.1:8765>). Click any product without losing the pipeline overview, permute axes, scrub or animate layers, isolate a layer, adjust the opacity of other layers, and inspect individual values. Horizontal and vertical dragging adjust the 3D stack separation within constrained inspection bounds, while double-clicking restores the home view. Two-dimensional products can switch between a heatmap and stacked 1D slices; axis order selects which dimension becomes the playable layer axis.

The viewer includes dark and light interface themes plus Spviz, Viridis, Plasma, Inferno, Magma, Cividis, Coolwarm, and Twilight color maps. Sequential maps fade their low end into the page theme, signed fields automatically use a zero-centered diverging map with zero transparent, and phase uses an opaque cyclic map. `NaN` remains transparent in every mode, so undefined CFAR edges and masked samples stay visually honest.

The inspector can export the selected axis-labeled layer as PNG, the current transparent stack as PNG, an animated GIF sweep through the selected depth axis, or the complete processing chain as PNG. Exports preserve the active axis permutation, coordinates, units, color limits, log mode, transparency, and selected layer where applicable.

The pixel-density control trades fidelity for interaction speed using an explicit samples-per-displayed-axis count. Small and medium products reach exact native resolution; very large dynamic runs use a 1024-pixel-per-axis visual safety ceiling so one gesture cannot allocate an unbounded browser canvas. The pipeline overview remains fixed at a lightweight 64 samples per axis.

The inspector aspect-ratio control offers **Data proportions** (the normal array width-to-height ratio), **Equal axes** (a square display extent), and **Fit view** (fill the available inspector area). The processing overview has a separate aspect control and defaults to data-proportional previews. Overview settings affect only the top processing graph; every product inspector opens independently in Data proportions mode.

## Observe your existing pipeline

```python
import numpy as np
import spviz

spviz.init("runs/my-run", name="My receiver")

# These functions belong to your application. spviz does not call them.
iq = read_receiver()
spviz.tap(
    iq,
    "Raw I/Q",
    axes=["channel", "pulse", "sample"],
    representation="magnitude",
    units="volts",
)

beamformed = beamform(iq)
spviz.tap(
    beamformed,
    "Beamformed I/Q",
    filename="beamformed_iq.npy",
    axes=["beam", "pulse", "sample"],
    scale="log",
    vmin=1e-4,
    vmax=2.0,
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

Coordinates may be numeric, categorical, or temporal. They are stored as separate NumPy arrays and loaded only when needed; long evenly spaced axes travel as compact start/step descriptions. The inspector presents permutations using axis names—not anonymous dimension numbers—and displays coordinate ranges, physical layer values, units, and coordinates for selected cells. For products above three dimensions, live-server controls for every non-view axis select the fixed source index without changing the three spatial axes. Static bundles preserve the captured default index for those extra dimensions, avoiding a combinatorial export for large tensors.

`tap()` returns the exact object it receives, so it can also be inserted inline without changing the chain:

```python
beamformed = spviz.tap(beamform(iq), "Beamformed", inputs=iq)
```

`filename=` controls the `.npy` filename inside the run's `arrays/` directory. It is intentionally a filename rather than an arbitrary path, keeping runs self-contained and portable. When omitted, `spviz` derives a safe filename from the display name and adds a suffix for repeated names.

`scale=` sets the product's default visualization scale to `"linear"` (the default) or `"log"`. It initializes the inspector and is also honored by the full-chain overview. Users can still toggle the selected product interactively.

`representation=` controls how scalar display values are derived without modifying the captured array. `"auto"` (the default) preserves signed real data and shows magnitude for complex data. Explicit choices are `"real"`, `"imag"`, `"magnitude"`, `"power"`, and `"phase"`; imaginary and phase views require complex-valued input. The choice applies consistently to 1D traces, maps, stacks, statistics, and exports.

`statistics=` controls the one-time range scan performed during capture. `"exact"` is the default and gives the range sliders true whole-product limits. `"sampled"` bounds that scan to roughly one million uniformly distributed values for very large products. `"none"` skips it completely and therefore requires explicit `vmin=` and `vmax=`. Rendering itself remains bounded independently of this capture-time choice.

`overview_aspect=` optionally overrides only that product's top processing-graph preview with `"data"`, `"equal"`, or `"fit"`. It does not change the product inspector. Without an override, the shared overview aspect control applies.

`vmin=` and `vmax=` set a product's initial absolute display range. In sequential maps, values at or below `vmin` are fully transparent and then fade smoothly into the selected color map; this lets background/noise disappear into either the dark or light theme without discarding the underlying captured data. Signed, phase, and binary products use their corresponding diverging, cyclic, and categorical opacity semantics. The viewer's range controls remain adjustable.

For code where wrapping a function is convenient, optional instrumentation observes its return value while leaving invocation and scheduling with the original application:

```python
spviz.init("runs/my-run")

@spviz.instrument(name="Filtered I/Q", axes=["channel", "sample"])
def filter_bank(iq):
    return existing_filter_implementation(iq)
```

Observed runs are portable directories containing `manifest.json` and standard NumPy `.npy` files. Source arrays are memory-mapped by the viewer. It requests only a bounded context stack plus the exact selected plane, so interaction cost follows display resolution rather than total source size. Rapid density changes are coalesced, stale draws are ignored, and render caches are bounded.

Run publication is transactional. `spviz.init(..., mode="replace")` (the default) stages a complete new run beside the destination and swaps it in only on explicit successful close. Use `mode="error"` when an existing destination should instead be treated as a mistake. For exception-aware automatic commit or rollback, use `Session` as a context manager.

Static exports are staged and swapped into place only after a complete successful build. By default each axis permutation is capped at 16 MB and the run at 256 MB while preserving every selectable depth layer; only plane density is reduced when needed. Tune those budgets for unusually large products:

```bash
spviz export-static runs/my-run site --max-volume-mb 32 --max-total-mb 512
```

## Current scope

- NumPy arrays with arbitrary dimensionality
- Directed product lineage and operation labels
- Local, dependency-light HTTP server
- Transparent stacked-slice volume rendering
- Axis permutation, layer playback/isolation, opacity, and value inspection
- Fourteen deterministic, physically grounded demonstrations spanning 1D, 2D, and 3D products

Live streaming, framework adapters, timeline comparison, and GPU-side capture remain future work; captured-run inspection is the intentionally focused core.

### Multiple plots from one tap

Pass a `views` dictionary to `spviz.tap`, `Recorder.tap`, `instrument`, or
`Session.capture` to show named plots of the same captured array:

```python
spviz.tap(iq, "Receiver I/Q", axes=["receiver", "sample"], primary_view="Amplitude", views={
    "Amplitude": {"representation": "magnitude", "units": "V"},
    "Phase": {"representation": "phase"},
})
```

Each tap occupies one horizontal pipeline column. Its views form a vertical
scrolling stack with the primary view centered and neighboring plots visible
above or below. Scroll or swipe vertically, click an adjacent plot, or use the
up/down buttons (or arrow keys while a plot is focused) to promote another view.
Horizontal scrolling still moves along the processing pipeline.

Set `primary_view="Amplitude"` to select the initial view by name. When omitted,
the first entry in `views` is primary. Unknown names are rejected. This option is
available on `spviz.tap`, `Recorder.tap`, both `instrument` APIs, `spviz.capture`,
and `Session.capture`. Changing the primary in the viewer applies to that tap
for the current page; it does not rewrite the capture or its API default.
The full-chain PNG keeps views vertically aligned around the current primaries.
 The array and coordinates are saved once, and downstream lineage still
refers to the original tap. Each plot has independent inspection controls.
Named views replace the single default plot; omitting `views` keeps existing
behavior. Both the local viewer and static exports support them.

View options are `representation`, `scale`, `vmin`, `vmax`, `units`,
`view_axes`, and `overview_aspect`. Unspecified options inherit the tap defaults,
except that changing representation resets bounds and units. Phase defaults to
linear scale, radians, and −π to π. A phase view requires complex data.
With `statistics="none"`, each view needs display bounds.

The radar gallery now includes amplitude and phase views. The new interferometry
example follows complex receiver voltages through baseline cross-products,
integrated visibilities, and a dirty angular image, alongside the aperture PSF:

```bash
python examples/interferometry.py --output runs/interferometry-demo
spviz serve runs/interferometry-demo
```

It simulates two mutually incoherent narrowband far-field sources with a linear
array. The image is one-dimensional in angle, retains aperture sidelobes, and
uses no range recovery or deconvolution.

### Interactive side-by-side comparison

Select a view and click **Add to comparison**. Each added pane is a full live
viewer with its own layer selection, camera/orientation, axis order, opacity,
color range, and export controls. You can add any number of panes, including
the same view more than once. Scroll horizontally to reach additional panes;
**Remove** closes one, and **Clear all** closes all comparison panes.

Enable **Link compatible viewers** to synchronize layer, orientation, axis
order, slice mode, aspect, isolation, and fixed-dimension indices between panes
with identical array shapes and the same displayed axis indices. Changes in
either pane drive the others. Display-range sliders, opacity, resolution, theme, color map, and play/pause also
synchronize. The initiating pane drives playback; pausing either pane pauses
the group. Unlinking stops group playback. Representations remain distinct,
and unsupported settings (such as logarithmic phase) stay disabled. Incompatible
panes stay independent. Linking is by array index, not physical coordinates.

Comparison panes work in local runs and static galleries. They are kept for the
current page session and close on reload. Each pane uses an isolated instance of
the regular viewer; the local server permits embedding only from the same origin.


Mini views occupy one pipeline column: the focused plot is full size, with quarter-size neighbors that can pass behind it. Scroll, drag, or use
Up/Down to rotate through the compact vertical depth carousel in either direction; the last
view wraps to the first. The list between the up/down arrows shows the available views and lets you select one directly; the API's `primary_view` remains the initial face.
Default previews are saved as 640 × 480 transparent PNGs, using the inspector’s initial slice, color transfer, and 96-density sample grid, with no volume download needed to display them. Local serving
reuses a bounded disk cache in the captured run's `.spviz-previews` directory and
revalidates PNGs when source files or display settings change. Read-only runs
still work, but cannot persist newly generated previews. Custom overview palettes
and aspect overrides use the interactive rendering path.

Static exports include PNGs and small 96-density context volumes. Large volumes
are stored as individual selectable layers, fetched on demand; small volumes stay
in a single file. Existing volume budgets still cover the layer data; PNGs, context
volumes and metadata add a small amount of storage. Thumbnails load near the
viewport with at most two active jobs. The viewer retains bounded in-memory data
and bitmap caches, while static assets also use the browser's HTTP cache.

Run `python examples/large_cube.py --output runs/large-cube` for a deterministic
16 × 256 × 1024 complex radar example (4.2 million samples per tap), with amplitude,
phase and real views before and after range–Doppler processing. It is also included
in the gallery.
