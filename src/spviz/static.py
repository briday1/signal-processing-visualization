from __future__ import annotations

import itertools
import json
import shutil
from importlib.resources import files
from pathlib import Path

from .server import RunStore


def export_static(run_dir: str | Path, output_dir: str | Path) -> Path:
    """Export a captured run as a serverless, GitHub Pages-compatible viewer."""
    store = RunStore(run_dir)
    output = Path(output_dir).expanduser().resolve()
    data = output / "data"
    coordinates = data / "coordinates"
    volumes = data / "volumes"
    coordinates.mkdir(parents=True, exist_ok=True)
    volumes.mkdir(parents=True, exist_ok=True)

    web = files("spviz").joinpath("web")
    for name in ("index.html", "style.css", "range.css", "app.js", "gif.js"):
        shutil.copyfile(web.joinpath(name), output / name)
    (output / "config.js").write_text(
        "window.SPVIZ_STATIC_BASE = './data'; window.SPVIZ_GALLERY_URL = '../';\n", encoding="utf-8"
    )
    (data / "run.json").write_text(json.dumps(store.manifest, separators=(",", ":")), encoding="utf-8")

    for product in store.manifest["products"]:
        product_id = product["id"]
        for axis in range(len(product["axes"])):
            payload = store.coordinates(product_id, axis)
            (coordinates / f"{product_id}--{axis}.json").write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        view_axes = [product["axes"].index(name) for name in product.get("view_axes", product["axes"][:3])]
        for permutation in itertools.permutations(view_axes):
            plane_axes = permutation[-2:] if len(permutation) > 1 else permutation
            native_limit = max(product["shape"][axis] for axis in plane_axes)
            metadata, body = store.volume_binary(product_id, list(permutation), native_limit)
            stem = f"{product_id}--{'-'.join(map(str, permutation))}"
            (volumes / f"{stem}.json").write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
            (volumes / f"{stem}.f32").write_bytes(body)
    return output
