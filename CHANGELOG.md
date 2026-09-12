# Changelog

## 0.2.0 — 2026-09-11

- Keep the observer passive: user code owns execution and `tap()` records values and lineage.
- Add axis coordinates, named permutations, non-view dimension selection, 1D traces, 2D stacked slices, constrained volume movement, aspect controls, themes, and scientific color maps.
- Add independent selected/context opacity, exact display limits, linear/log defaults, binary-mask rendering, PNG/GIF/full-chain export, and configurable pixel density.
- Bound dynamic volume transfer and cache work for large memory-mapped arrays while loading the selected plane exactly.
- Add explicit real, imaginary, magnitude, power, and phase representations without changing source data.
- Harden run validation, atomic capture/static export, filename handling, HTTP asset isolation, metadata rendering, concurrency, and release checks.
- Expand the Pages gallery to fourteen deterministic signal-processing pipelines, including radar, communications, GNSS, ultrasound, CT, channelization, seismic, ECG, acoustics, and machinery diagnostics.

## 0.1.0 — 2026-09-11

- Initial PyPI release with passive NumPy capture, the local viewer, static export, and radar example.
