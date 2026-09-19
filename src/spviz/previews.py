"""Deterministic high-resolution PNG previews, without a plotting dependency."""

from __future__ import annotations

import struct
import zlib

import numpy as np


def preview_png(store, product_id: str) -> bytes:
    product = store.products[product_id]
    axes = [
        product["axes"].index(name)
        for name in product.get("view_axes", product["axes"][:3])
    ]
    metadata, body = store.volume_binary(product_id, axes, 512, depth_limit=6)
    values = np.frombuffer(body, dtype="<f4").reshape(metadata["shape"])
    finite = values[np.isfinite(values)]
    stats = product.get("stats", {})
    low = next(
        (
            value
            for value in [
                product.get("display_min"),
                stats.get("display_min"),
                stats.get("value_min"),
                stats.get("min"),
            ]
            if value is not None
        ),
        float(finite.min()) if finite.size else 0,
    )
    high = next(
        (
            value
            for value in [
                product.get("display_max"),
                stats.get("display_max"),
                stats.get("value_max"),
                stats.get("max"),
            ]
            if value is not None
        ),
        float(finite.max()) if finite.size else 1,
    )
    high = max(high, low + 1e-12)
    phase = product.get("representation") == "phase"
    logarithmic = not phase and product.get("scale") == "log"
    diverging = not phase and not logarithmic and low < 0 < high
    if logarithmic:
        floor = max(0, low, high * 1e-6, 1e-12)
        values = np.log10(np.maximum(values, floor))
        low, high = np.log10(floor), np.log10(max(high, floor))
    normalized = np.clip((values - low) / max(high - low, 1e-30), 0, 1)
    normalized = np.nan_to_num(normalized)
    colors = np.array(
        [[23, 33, 58], [97, 119, 255], [40, 191, 167], [237, 152, 95], [235, 97, 112]],
        dtype=float,
    )
    if phase:
        colors = np.array(
            [
                [226, 217, 226],
                [98, 118, 186],
                [53, 109, 108],
                [165, 123, 53],
                [181, 54, 90],
                [226, 217, 226],
            ],
            dtype=float,
        )
    elif diverging:
        colors = np.array(
            [
                [59, 76, 192],
                [185, 208, 249],
                [221, 221, 221],
                [247, 184, 156],
                [180, 4, 38],
            ],
            dtype=float,
        )
    if product.get("units") == "binary" or product.get("dtype") == "bool":
        colors = np.array([[235, 97, 112], [235, 97, 112]], dtype=float)
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
        width = 490 if mode == "fit" else max(1, round(min(490, 340 * aspect)))
        height = 340 if mode == "fit" else max(1, round(min(340, 490 / aspect)))
        rows = np.rint(np.linspace(0, values.shape[1] - 1, height)).astype(int)
        columns = np.rint(np.linspace(0, values.shape[2] - 1, width)).astype(int)
        for layer in reversed(range(len(values))):
            t = normalized[layer][np.ix_(rows, columns)]
            sampled = values[layer][np.ix_(rows, columns)]
            strength = np.abs(sampled) / max(abs(low), abs(high)) if diverging else t
            fade = np.clip(strength / 0.12, 0, 1)
            fade = np.ones_like(t) if phase else fade * fade * (3 - 2 * fade)
            position = t * (len(colors) - 1)
            first = position.astype(int)
            mix = (position - first)[..., None]
            rgb = (
                colors[first] * (1 - mix)
                + colors[np.minimum(first + 1, len(colors) - 1)] * mix
            )
            alpha = np.where(
                np.isfinite(values[layer][np.ix_(rows, columns)]),
                fade * (0.72 if layer == 0 else 0.32),
                0,
            )
            x, y = (
                (640 - width) // 2 - 30 + layer * 14,
                (480 - height) // 2 + 30 - layer * 14,
            )
            target = pixels[y : y + height, x : x + width]
            old_alpha = target[..., 3] / 255
            out_alpha = alpha + old_alpha * (1 - alpha)
            target[..., :3] = (
                rgb * alpha[..., None]
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
