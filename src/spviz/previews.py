"""Deterministic high-resolution PNG previews, without a plotting dependency."""

from __future__ import annotations

import struct
import zlib

import numpy as np

# Keep these transfer functions identical to web/app.js bitmapFor. The parity
# test executes the browser function and compares its RGBA bytes against these.
PREVIEW_VERSION = 4

PALETTES = {
    "spviz": ["17213a", "6177ff", "28bfa7", "ed985f", "eb6170"],
    "coolwarm": ["3b4cc0", "7093f3", "b9d0f9", "dddddd", "f7b89c", "d95847", "b40426"],
    "twilight": [
        "e2d9e2",
        "9e9ac8",
        "6276ba",
        "3e4a89",
        "356d6c",
        "587d43",
        "a57b35",
        "c85a32",
        "b5365a",
        "7e3f78",
        "e2d9e2",
    ],
}


def display_rgba(values, low, high, *, phase=False, log=False, binary=False):
    values = np.asarray(values, dtype=float)
    cyclic = phase and not binary
    diverging = not cyclic and not log and low < 0 < high
    valid = np.isfinite(values)
    if not cyclic and not diverging:
        valid &= values > low
    values = np.nan_to_num(values, nan=low, posinf=high, neginf=low)
    if log:
        log_min = np.log(max(1e-12, low))
        span = max(1e-12, np.log(max(low * 1.0001, high)) - log_min)
        t = (np.log(np.maximum(1e-12, values)) - log_min) / span
    else:
        t = (values - low) / max(1e-12, high - low)
    if diverging:
        strength = np.minimum(
            1, np.where(values < 0, np.abs(values / low), values / high)
        )
        t = 0.5 + np.where(values < 0, -0.5 * strength, 0.5 * strength)
    t = np.clip(t, 0, 1)
    name = (
        "spviz"
        if binary
        else "twilight"
        if cyclic
        else "coolwarm"
        if diverging
        else "spviz"
    )
    colors = np.array(
        [[int(c[i : i + 2], 16) for i in (0, 2, 4)] for c in PALETTES[name]]
    )
    position = (t if cyclic or diverging else t**0.72) * (len(colors) - 1)
    first = position.astype(int)
    mix = (position - first)[..., None]
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.floor(
        colors[first] * (1 - mix)
        + colors[np.minimum(first + 1, len(colors) - 1)] * mix
        + 0.5
    )
    fade = np.clip((strength if diverging else t) / 0.12, 0, 1)
    alpha = np.ones_like(t) if cyclic else fade * fade * (3 - 2 * fade)
    rgba[..., 3] = np.floor(alpha * 255 + 0.5)
    rgba[~valid] = 0
    return rgba


def preview_png(store, product_id: str, *, sampled=None, selected=None) -> bytes:
    product = store.products[product_id]
    axes = [
        product["axes"].index(name)
        for name in product.get("view_axes", product["axes"][:3])
    ]
    metadata, body = sampled or store.volume_binary(
        product_id, axes, 96, depth_limit=12
    )
    values = np.frombuffer(body, dtype="<f4").reshape(metadata["shape"]).copy()
    if selected is not None and len(axes) == 1:
        values = selected[None, ...]
    finite = values[np.isfinite(values)]
    stats = product.get("stats", {})
    low = next(
        (
            v
            for v in [
                product.get("display_min"),
                stats.get("display_min"),
                stats.get("value_min"),
                stats.get("min"),
            ]
            if v is not None
        ),
        float(finite.min()) if finite.size else 0,
    )
    high = next(
        (
            v
            for v in [
                product.get("display_max"),
                stats.get("display_max"),
                stats.get("value_max"),
                stats.get("max"),
            ]
            if v is not None
        ),
        float(finite.max()) if finite.size else 1,
    )
    phase = product.get("representation") == "phase"
    binary = product.get("units") == "binary" or product.get("dtype") == "bool"
    log = not phase and not binary and product.get("scale") == "log"
    if log:
        low = max(0, low)
    high = max(high, low + 1e-12)
    if log:
        low = max(low, high * 1e-6, 1e-12)
    rgba = display_rgba(values, low, high, phase=phase, log=log, binary=binary)
    if log:
        normalized = (np.log(np.maximum(values, 1e-12)) - np.log(low)) / max(
            1e-12, np.log(high) - np.log(low)
        )
    else:
        normalized = (values - low) / max(1e-12, high - low)
    normalized = np.nan_to_num(np.clip(normalized, 0, 1))
    pixels = np.zeros((480, 640, 4), dtype=np.uint8)
    if len(axes) == 1:
        ys = 420 - np.rint(normalized[0, 0] * 360).astype(int)
        xs = np.linspace(40, 600, len(ys)).astype(int)
        for index in range(max(1, len(xs) - 1)):
            end = min(index + 1, len(xs) - 1)
            if not np.isfinite(values[0, 0, [index, end]]).all():
                continue
            count = max(abs(xs[end] - xs[index]), abs(ys[end] - ys[index])) + 1
            x = np.linspace(xs[index], xs[end], count).astype(int)
            y = np.linspace(ys[index], ys[end], count).astype(int)
            for offset in (-1, 0, 1):
                pixels[y + offset, x] = [97, 119, 255, 255]
    else:
        aspect = metadata["source_plane_shape"][1] / metadata["source_plane_shape"][0]
        mode = product.get("overview_aspect", "data")
        if mode == "equal":
            aspect = 1
        slots = len(values)
        available_width = max(80, 640 - 190 - 16 * (slots - 1))
        available_height = max(80, 480 - 120 - 11 * (slots - 1))
        width = max(
            1,
            round(
                available_width
                if mode == "fit"
                else min(available_width, available_height * aspect)
            ),
        )
        height = max(1, round(available_height if mode == "fit" else width / aspect))
        origin_x = round((640 - width - 16 * (slots - 1)) / 2 + 35)
        origin_y = round((480 - height - 11 * (slots - 1) - 50) / 2 + 11 * (slots - 1))
        for layer in reversed(range(slots)):
            plane = (
                display_rgba(selected, low, high, phase=phase, log=log, binary=binary)
                if layer == 0 and selected is not None
                else rgba[layer]
            )
            rows = np.floor(np.arange(height) * plane.shape[0] / height).astype(int)
            columns = np.floor(np.arange(width) * plane.shape[1] / width).astype(int)
            image = plane[np.ix_(rows, columns)]
            alpha = image[..., 3] / 255 * (1 if layer == 0 else 0.25)
            x, y = origin_x + layer * 16, origin_y - layer * 11
            target = pixels[y : y + height, x : x + width]
            old_alpha = target[..., 3] / 255
            out_alpha = alpha + old_alpha * (1 - alpha)
            target[..., :3] = (
                image[..., :3] * alpha[..., None]
                + target[..., :3] * (old_alpha * (1 - alpha))[..., None]
            ) / np.maximum(out_alpha[..., None], 1e-20)
            target[..., 3] = out_alpha * 255

    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data))
        )

    raw = b"".join(b"\0" + row.tobytes() for row in pixels)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 640, 480, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
