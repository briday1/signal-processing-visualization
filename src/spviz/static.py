from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import shutil
import tempfile
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

from .server import RunStore

DEFAULT_MAX_VOLUME_BYTES = 16_000_000
DEFAULT_MAX_TOTAL_VOLUME_BYTES = 256_000_000
_STATIC_MARKER = ".spviz-static"


def _positive_budget(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 4:
        raise ValueError(f"{name} must be an integer of at least 4 bytes or None")
    return value


def _recognizable_export(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not any(path.iterdir()):
        return True
    marker = path / _STATIC_MARKER
    if marker.exists() and (
        not marker.is_file()
        or marker.read_text(encoding="utf-8") != "spviz static export\n"
    ):
        return False
    manifest_path = path / "data" / "run.json"
    config_path = path / "config.js"
    if not manifest_path.is_file() or not config_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return manifest.get(
        "format"
    ) == "spviz-run" and "SPVIZ_STATIC_BASE" in config_path.read_text(encoding="utf-8")


def _volume_count(manifest: dict) -> int:
    count = 0
    for product in manifest["products"]:
        axis_count = len(product.get("view_axes", product["axes"][:3]))
        count += math.factorial(axis_count)
    return count


def _largest_limit(rows: int, columns: int, maximum_values: int) -> int:
    """Largest common axis limit whose rectangular plane fits the budget."""
    low, high = 1, max(rows, columns)
    while low < high:
        candidate = (low + high + 1) // 2
        if min(rows, candidate) * min(columns, candidate) <= maximum_values:
            low = candidate
        else:
            high = candidate - 1
    return low


def _replace_export(staged: Path, output: Path) -> None:
    """Swap a complete staged site into place, restoring the old site on error."""
    if not output.exists():
        os.replace(staged, output)
        return
    backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.backup-", dir=output.parent))
    backup.rmdir()
    os.replace(output, backup)
    try:
        os.replace(staged, output)
    except BaseException:
        os.replace(backup, output)
        raise
    try:
        shutil.rmtree(backup)
    except OSError:
        pass


def _source_signature(store: RunStore) -> tuple:
    """Fingerprint the source generation so a static site cannot mix runs."""
    root = store.path.stat()
    manifest = store.path / "manifest.json"
    files = [manifest]
    for product in store.manifest["products"]:
        files.append(store._run_file(product["file"], product=product["id"]))
        files.extend(
            store._run_file(descriptor["file"], product=product["id"])
            for descriptor in product.get("coordinates", {}).values()
        )
    file_state = tuple(
        (
            path.stat().st_dev,
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in files
    )
    return (
        root.st_dev,
        root.st_ino,
        hashlib.sha256(manifest.read_bytes()).digest(),
        file_state,
    )


def _export_into(store: RunStore, output: Path, per_volume_budget: int) -> None:
    data = output / "data"
    coordinates = data / "coordinates"
    volumes = data / "volumes"
    coordinates.mkdir(parents=True)
    volumes.mkdir(parents=True)
    previews = data / "previews"
    previews.mkdir()
    layers = data / "layers"
    layers.mkdir()
    contexts = data / "contexts"
    contexts.mkdir()

    web = files("spviz").joinpath("web")
    for name in ("index.html", "style.css", "range.css", "app.js", "gif.js"):
        shutil.copyfile(web.joinpath(name), output / name)
    (output / "config.js").write_text(
        "window.SPVIZ_STATIC_BASE = './data'; window.SPVIZ_GALLERY_URL = '../';\n",
        encoding="utf-8",
    )

    # Content-address UI assets as well as data: Pages may otherwise combine a
    # fresh manifest with the previous deployment's cached JavaScript and CSS.
    index = (output / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "range.css", "config.js", "gif.js", "app.js"):
        revision = hashlib.sha256((output / name).read_bytes()).hexdigest()[:16]
        index = index.replace(f'"./{name}"', f'"./{name}?v={revision}"')
    (output / "index.html").write_text(index, encoding="utf-8")

    export_manifest = deepcopy(store.manifest)
    export_manifest["static_export"] = {
        "revision": hashlib.sha256(repr(_source_signature(store)).encode()).hexdigest()[
            :16
        ],
        "progressive": True,
        "previews": True,
        "volume_count": _volume_count(store.manifest),
        "max_bytes_per_volume": per_volume_budget,
        "max_total_volume_bytes": per_volume_budget * _volume_count(store.manifest),
        "float_encoding": "little-endian float32",
    }
    exported_volume_bytes = 0
    capped_volumes = 0
    for product in store.manifest["products"]:
        product_id = product["id"]
        _, png = store.preview(product_id)
        (previews / f"{product_id}.png").write_bytes(png)
        for axis in range(len(product["axes"])):
            payload = store.coordinates(product_id, axis)
            (coordinates / f"{product_id}--{axis}.json").write_text(
                json.dumps(payload, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )
        view_axes = [
            product["axes"].index(name)
            for name in product.get("view_axes", product["axes"][:3])
        ]
        for permutation in itertools.permutations(view_axes):
            if len(permutation) == 1:
                native_depth, source_rows, source_columns = (
                    1,
                    1,
                    product["shape"][permutation[0]],
                )
            elif len(permutation) == 2:
                native_depth = 1
                source_rows, source_columns = (
                    product["shape"][axis] for axis in permutation
                )
            else:
                native_depth = product["shape"][permutation[0]]
                source_rows, source_columns = (
                    product["shape"][axis] for axis in permutation[-2:]
                )
            maximum_values = per_volume_budget // 4
            if native_depth > maximum_values:
                raise ValueError(
                    f"Cannot export {product_id!r} permutation {permutation}: preserving its {native_depth} "
                    f"layers requires at least {native_depth * 4} bytes; increase max_volume_bytes"
                )
            plane_budget = max(1, maximum_values // native_depth)
            native_limit = max(source_rows, source_columns)
            export_limit = min(
                native_limit, _largest_limit(source_rows, source_columns, plane_budget)
            )
            metadata, body = store.volume_binary(
                product_id,
                list(permutation),
                export_limit,
                depth_limit=native_depth,
                max_values=maximum_values,
            )
            if len(body) > per_volume_budget:
                raise RuntimeError(
                    f"Static volume budget was exceeded for {product_id!r}"
                )
            plane_capped = (
                metadata["rows"] < source_rows or metadata["columns"] < source_columns
            )
            depth_capped = metadata["depth"] < native_depth
            metadata["static_export"] = {
                "payload_bytes": len(body),
                "max_payload_bytes": per_volume_budget,
                "source_depth": native_depth,
                "exported_depth": metadata["depth"],
                "source_plane_shape": [source_rows, source_columns],
                "exported_plane_shape": [metadata["rows"], metadata["columns"]],
                "plane_capped": plane_capped,
                "depth_capped": depth_capped,
                "requested_axis_limit": native_limit,
                "exported_axis_limit": export_limit,
            }
            capped_volumes += int(plane_capped or depth_capped)
            exported_volume_bytes += len(body)
            stem = f"{product_id}--{'-'.join(map(str, permutation))}"
            metadata["layer_chunks"] = len(body) > 256_000
            (volumes / f"{stem}.json").write_text(
                json.dumps(metadata, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )
            if metadata["layer_chunks"]:
                plane_bytes = metadata["rows"] * metadata["columns"] * 4
                for layer in range(metadata["depth"]):
                    (layers / f"{stem}--{layer}.f32").write_bytes(
                        body[layer * plane_bytes : (layer + 1) * plane_bytes]
                    )
            else:
                (volumes / f"{stem}.f32").write_bytes(body)
            context_metadata, context_body = store.volume_binary(
                product_id,
                list(permutation),
                96,
                depth_limit=12,
            )
            (contexts / f"{stem}.json").write_text(
                json.dumps(context_metadata, separators=(",", ":")), encoding="utf-8"
            )
            (contexts / f"{stem}.f32").write_bytes(context_body)

    export_manifest["static_export"].update(
        {"volume_bytes": exported_volume_bytes, "capped_volumes": capped_volumes}
    )
    (data / "run.json").write_text(
        json.dumps(export_manifest, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    (output / _STATIC_MARKER).write_text("spviz static export\n", encoding="utf-8")


def export_static(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    max_volume_bytes: int = DEFAULT_MAX_VOLUME_BYTES,
    max_total_volume_bytes: int | None = DEFAULT_MAX_TOTAL_VOLUME_BYTES,
) -> Path:
    """Export a captured run through a bounded, transactional directory swap.

    Plane resolution is reduced uniformly when necessary. Every source layer is
    retained so layer selection remains exact in the serverless viewer.
    """
    maximum_volume = _positive_budget(max_volume_bytes, "max_volume_bytes")
    maximum_total = _positive_budget(max_total_volume_bytes, "max_total_volume_bytes")
    if maximum_volume is None:
        raise ValueError("max_volume_bytes cannot be None")
    store = RunStore(run_dir)
    source_signature = _source_signature(store)
    requested_output = Path(output_dir).expanduser()
    if requested_output.is_symlink():
        raise ValueError("output_dir cannot be a symbolic link")
    output = requested_output.resolve()
    run = store.path.resolve()
    if (
        output == Path(output.anchor)
        or output == run
        or output.is_relative_to(run)
        or run.is_relative_to(output)
    ):
        raise ValueError("output_dir must not overlap the captured run directory")
    if output.exists() and not output.is_dir():
        raise ValueError("output_dir must be a directory")
    if output.exists() and not _recognizable_export(output):
        raise ValueError(
            "Refusing to replace a non-empty directory that is not an spviz static export"
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    volume_count = _volume_count(store.manifest)
    per_volume_budget = maximum_volume
    if maximum_total is not None and volume_count:
        per_volume_budget = min(per_volume_budget, maximum_total // volume_count)
    if per_volume_budget < 4:
        raise ValueError("max_total_volume_bytes is too small for this run")

    staged = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent)
    )
    try:
        _export_into(store, staged, per_volume_budget)
        if _source_signature(store) != source_signature:
            raise RuntimeError(
                "Captured run changed during static export; retry the export"
            )
        _replace_export(staged, output)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return output
