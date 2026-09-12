from __future__ import annotations

import json
import mimetypes
import re
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

MAX_RENDER_DIMENSION = 1024
MAX_VOLUME_DEPTH = 64
MAX_VOLUME_VALUES = 4_000_000
WEB_ASSETS = {"index.html", "style.css", "range.css", "app.js", "gif.js"}
REPRESENTATIONS = {"auto", "real", "imag", "magnitude", "power", "phase"}
MAX_PRODUCT_ID_LENGTH = 128
PRODUCT_ID_PATTERN = re.compile(
    rf"[a-z0-9][a-z0-9_-]{{0,{MAX_PRODUCT_ID_LENGTH - 1}}}\Z"
)


def _uniform_indices(size: int, limit: int) -> np.ndarray:
    count = min(size, max(1, limit))
    if count == size:
        return np.arange(size, dtype=np.intp)
    return np.rint(np.linspace(0, size - 1, count)).astype(np.intp)


def _represented(array: np.ndarray, representation: str) -> np.ndarray:
    """Convert sampled real or complex values to a scalar display representation."""
    resolved = (
        "magnitude"
        if representation == "auto" and np.iscomplexobj(array)
        else representation
    )
    if resolved in {"auto", "real"}:
        return np.real(array)
    if resolved == "imag":
        return np.imag(array)
    if resolved == "magnitude":
        source = (
            array.astype(np.float64, copy=False) if array.dtype.kind in "iu" else array
        )
        return np.abs(source)
    if resolved == "power":
        source = (
            array.astype(np.float64, copy=False) if array.dtype.kind in "iu" else array
        )
        with np.errstate(over="ignore", invalid="ignore"):
            return np.square(np.abs(source))
    if resolved == "phase":
        return np.angle(array)
    raise ValueError(f"Unknown representation: {representation!r}")


def _float32_display(array: np.ndarray, representation: str) -> np.ndarray:
    represented = _represented(array, representation)
    generated_invalid = np.isfinite(array) & ~np.isfinite(represented)
    if np.any(generated_invalid):
        raise ValueError(
            f"Representation {representation!r} overflows for these values"
        )
    finite = represented[np.isfinite(represented)]
    if finite.size and np.max(np.abs(finite)) > np.finfo(np.float32).max:
        raise ValueError(
            "Displayed values exceed the float32 renderer range; scale the data before capture"
        )
    return np.asarray(represented, dtype="<f4")


def _linear_parameters(array: np.ndarray) -> tuple[float, float] | None:
    """Detect an evenly spaced numeric coordinate in bounded memory."""
    length = len(array)
    if length < 2 or array.dtype.kind not in "iuf":
        return None
    start = float(array[0])
    stop = float(array[-1])
    step = (stop - start) / (length - 1)
    tolerance = max(1e-12, abs(stop - start) * 1e-10)
    if not np.isfinite([start, stop, step]).all():
        return None
    chunk_size = 1_000_000
    for first in range(0, length, chunk_size):
        last = min(length, first + chunk_size)
        values = np.asarray(array[first:last], dtype=np.float64)
        expected = start + step * np.arange(first, last, dtype=np.float64)
        if not np.allclose(values, expected, rtol=1e-8, atol=tolerance):
            return None
    return start, step


class RunStore:
    def __init__(self, run_dir: str | Path):
        self.path = Path(run_dir).expanduser().resolve()
        manifest_path = self.path / "manifest.json"
        if manifest_path.is_symlink():
            raise ValueError("The spviz manifest cannot be a symbolic link")
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Not an spviz run: {manifest_path} is missing")
        arrays_path = self.path / "arrays"
        if (
            arrays_path.is_symlink()
            or not arrays_path.is_dir()
            or arrays_path.resolve().parent != self.path
        ):
            raise ValueError(
                f"Not an spviz run: {arrays_path} must be a real directory"
            )
        self._manifest_path = manifest_path
        self._manifest_mtime_ns = -1
        self._lock = threading.RLock()
        self.refresh()

    def refresh(self) -> None:
        """Reload a run that was regenerated while its viewer stays open."""
        with self._lock:
            if self._manifest_path.is_symlink():
                raise ValueError("The spviz manifest cannot be a symbolic link")
            mtime_ns = self._manifest_path.stat().st_mtime_ns
            if mtime_ns == self._manifest_mtime_ns:
                return
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            products = self._validate_manifest(manifest)
            self.manifest = manifest
            self.products = products
            self._manifest_mtime_ns = mtime_ns

    def _run_file(self, value: str, *, product: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"Invalid file for product {product!r}")
        relative = Path(value)
        arrays_path = self.path / "arrays"
        arrays = arrays_path.resolve()
        if (
            arrays_path.is_symlink()
            or not arrays_path.is_dir()
            or arrays.parent != self.path
        ):
            raise ValueError(
                f"Not an spviz run: {arrays_path} must be a real directory"
            )
        candidate = (self.path / relative).resolve()
        if relative.is_absolute() or candidate.parent != arrays:
            raise ValueError(
                f"Product {product!r} references a file outside the run arrays directory"
            )
        if not candidate.is_file():
            raise ValueError(f"Product {product!r} references a missing file: {value}")
        return candidate

    def _validate_manifest(self, manifest: dict) -> dict[str, dict]:
        if not isinstance(manifest, dict) or manifest.get("format") != "spviz-run":
            raise ValueError("Unsupported or malformed spviz manifest")
        try:
            json.dumps(manifest, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("Manifest must contain finite JSON values") from error
        if manifest.get("version") != 1:
            raise ValueError(
                f"Unsupported spviz manifest version: {manifest.get('version')!r}"
            )
        if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
            raise ValueError("Manifest name must be a non-empty string")
        if not isinstance(manifest.get("metadata", {}), dict):
            raise TypeError("Manifest metadata must be an object")
        entries = manifest.get("products")
        if not isinstance(entries, list):
            raise TypeError("Manifest products must be a list")
        products: dict[str, dict] = {}
        referenced_files: set[Path] = set()
        for product in entries:
            if not isinstance(product, dict):
                raise TypeError("Every manifest product must be an object")
            product_id = product.get("id")
            shape = product.get("shape")
            axes = product.get("axes")
            if (
                not isinstance(product_id, str)
                or PRODUCT_ID_PATTERN.fullmatch(product_id) is None
                or product_id in products
            ):
                raise ValueError(f"Invalid or duplicate product id: {product_id!r}")
            if not isinstance(product.get("name"), str) or not product["name"].strip():
                raise ValueError(f"Product {product_id!r} has an invalid name")
            if (
                not isinstance(shape, list)
                or not shape
                or any(type(size) is not int or size < 1 for size in shape)
            ):
                raise ValueError(f"Product {product_id!r} has an invalid shape")
            if (
                not isinstance(axes, list)
                or len(axes) != len(shape)
                or len(set(axes)) != len(axes)
                or not all(isinstance(axis, str) and axis for axis in axes)
            ):
                raise ValueError(f"Product {product_id!r} has invalid axes")
            view_axes = product.get("view_axes", axes[:3])
            if (
                not isinstance(view_axes, list)
                or not 1 <= len(view_axes) <= min(3, len(axes))
                or len(set(view_axes)) != len(view_axes)
                or any(axis not in axes for axis in view_axes)
            ):
                raise ValueError(f"Product {product_id!r} has invalid view_axes")
            try:
                dtype = np.dtype(product["dtype"])
            except (KeyError, TypeError) as error:
                raise ValueError(
                    f"Product {product_id!r} has an invalid dtype"
                ) from error
            if dtype.hasobject or dtype.kind not in "biufc":
                raise ValueError(
                    f"Product {product_id!r} has unsupported dtype {dtype}"
                )
            representation = product.get("representation", "auto")
            if representation not in REPRESENTATIONS:
                raise ValueError(
                    f"Product {product_id!r} has an invalid representation"
                )
            if representation in {"imag", "phase"} and dtype.kind != "c":
                raise ValueError(
                    f"Product {product_id!r} uses {representation!r} with real-valued data"
                )
            array_path = self._run_file(product.get("file"), product=product_id)
            if array_path in referenced_files:
                raise ValueError(f"Product {product_id!r} reuses an array file")
            referenced_files.add(array_path)
            try:
                stored = np.load(array_path, mmap_mode="r", allow_pickle=False)
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"Product {product_id!r} has an unreadable array"
                ) from error
            if list(stored.shape) != shape or stored.dtype != dtype:
                raise ValueError(
                    f"Product {product_id!r} shape or dtype does not match its array"
                )
            if product.get("bytes", int(stored.nbytes)) != int(stored.nbytes):
                raise ValueError(f"Product {product_id!r} has an invalid byte count")
            if product.get("scale", "linear") not in {"linear", "log"}:
                raise ValueError(f"Product {product_id!r} has an invalid scale")
            if product.get("overview_aspect") not in {None, "data", "equal", "fit"}:
                raise ValueError(
                    f"Product {product_id!r} has an invalid overview aspect"
                )
            upstream = product.get("upstream", [])
            if (
                not isinstance(upstream, list)
                or not all(isinstance(parent, str) and parent for parent in upstream)
                or len(upstream) != len(set(upstream))
            ):
                raise ValueError(f"Product {product_id!r} has invalid upstream lineage")
            coordinates = product.get("coordinates", {})
            if not isinstance(coordinates, dict) or any(
                axis not in axes for axis in coordinates
            ):
                raise ValueError(f"Product {product_id!r} has invalid coordinates")
            for axis, descriptor in coordinates.items():
                if (
                    not isinstance(descriptor, dict)
                    or descriptor.get("length") != shape[axes.index(axis)]
                ):
                    raise ValueError(
                        f"Product {product_id!r} has invalid coordinates for {axis!r}"
                    )
                coordinate_path = self._run_file(
                    descriptor.get("file"), product=product_id
                )
                if coordinate_path in referenced_files:
                    raise ValueError(
                        f"Product {product_id!r} reuses an array or coordinate file"
                    )
                referenced_files.add(coordinate_path)
                try:
                    coordinate = np.load(
                        coordinate_path, mmap_mode="r", allow_pickle=False
                    )
                except (OSError, ValueError) as error:
                    raise ValueError(
                        f"Product {product_id!r} has unreadable coordinates for {axis!r}"
                    ) from error
                if coordinate.ndim != 1 or len(coordinate) != descriptor["length"]:
                    raise ValueError(
                        f"Product {product_id!r} coordinate data for {axis!r} has the wrong shape"
                    )
            products[product_id] = product
        for product_id, product in products.items():
            for parent in product.get("upstream", []):
                if parent not in products:
                    raise ValueError(
                        f"Product {product_id!r} references unknown upstream product {parent!r}"
                    )
                if parent == product_id:
                    raise ValueError(f"Product {product_id!r} cannot depend on itself")

        children: dict[str, list[str]] = {product_id: [] for product_id in products}
        indegree = {
            product_id: len(product.get("upstream", []))
            for product_id, product in products.items()
        }
        for product_id, product in products.items():
            for parent in product.get("upstream", []):
                children[parent].append(product_id)
        ready = deque(
            product_id for product_id, degree in indegree.items() if degree == 0
        )
        visited = 0
        while ready:
            parent = ready.popleft()
            visited += 1
            for child in children[parent]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(products):
            raise ValueError("Product lineage contains a cycle")
        return products

    def array(self, product_id: str) -> np.ndarray:
        product = self.products.get(product_id)
        if product is None:
            raise KeyError(product_id)
        return np.load(
            self._run_file(product["file"], product=product_id),
            mmap_mode="r",
            allow_pickle=False,
        )

    def coordinates(self, product_id: str, axis: int, limit: int = 4096) -> dict:
        product = self.products.get(product_id)
        if product is None or not 0 <= axis < len(product["axes"]):
            raise KeyError(product_id)
        axis_name = product["axes"][axis]
        length = product["shape"][axis]
        descriptor = product.get("coordinates", {}).get(axis_name)
        if descriptor is None:
            return {
                "axis": axis,
                "name": axis_name,
                "units": None,
                "length": length,
                "encoding": "linear",
                "start": 0,
                "step": 1,
                "implicit": True,
            }
        array = np.load(
            self._run_file(descriptor["file"], product=product_id),
            mmap_mode="r",
            allow_pickle=False,
        )
        payload = {
            "axis": axis,
            "name": axis_name,
            "units": descriptor.get("units"),
            "length": length,
            "implicit": False,
        }
        linear = _linear_parameters(array) if length > limit else None
        if linear is not None:
            start, step = linear
            return {
                **payload,
                "encoding": "linear",
                "start": start,
                "step": step,
            }
        indices = _uniform_indices(length, limit)
        sampled = array[indices]
        if sampled.dtype.kind == "M":
            values = np.datetime_as_string(sampled).tolist()
        elif sampled.dtype.kind == "m":
            values = sampled.astype(str).tolist()
        else:
            values = sampled.tolist()
        return {
            **payload,
            "encoding": "values" if len(indices) == length else "sampled",
            "indices": indices.tolist(),
            "values": values,
        }

    def _slice_plane(
        self,
        product_id: str,
        permutation: list[int],
        layer: int,
        limit: int = 96,
        indices: dict[int, int] | None = None,
        representation: str | None = None,
    ) -> tuple[dict, np.ndarray]:
        array = self.array(product_id)
        product = self.products[product_id]
        if not 1 <= len(permutation) <= min(3, array.ndim):
            raise ValueError("Permutation must select between 1 and 3 display axes")
        if len(set(permutation)) != len(permutation) or any(
            axis < 0 or axis >= array.ndim for axis in permutation
        ):
            raise ValueError("Display axes must be unique valid array axes")
        indices = indices or {}
        selectors: list[int | slice] = []
        remaining_axes: list[int] = []
        for axis, size in enumerate(array.shape):
            if axis in permutation:
                selectors.append(slice(None))
                remaining_axes.append(axis)
            else:
                selected = indices.get(axis, 0)
                if not 0 <= selected < size:
                    raise ValueError(
                        f"Index {selected} is out of range for axis {axis}"
                    )
                selectors.append(selected)
        view = array[tuple(selectors)]
        view = np.transpose(view, [remaining_axes.index(axis) for axis in permutation])
        if len(permutation) == 1:
            view = view[np.newaxis, np.newaxis, :]
        elif len(permutation) == 2:
            view = view[np.newaxis, :, :]
        depth = view.shape[0]
        layer = max(0, min(layer, depth - 1))
        selected_representation = representation or product.get(
            "representation", "auto"
        )
        if selected_representation not in REPRESENTATIONS:
            raise ValueError(f"Unknown representation: {selected_representation!r}")
        if selected_representation in {"imag", "phase"} and not np.iscomplexobj(array):
            raise ValueError(
                f"Representation {selected_representation!r} requires complex-valued data"
            )
        plane = _float32_display(view[layer], selected_representation)
        source_rows, source_columns = plane.shape
        row_indices = _uniform_indices(source_rows, limit)
        column_indices = _uniform_indices(source_columns, limit)
        plane = plane[np.ix_(row_indices, column_indices)]
        finite = plane[np.isfinite(plane)]
        peak = float(finite.max()) if finite.size else 0.0
        metadata = {
            "product": product_id,
            "permutation": permutation,
            "indices": indices,
            "layer": layer,
            "depth": depth,
            "shape": list(view.shape),
            "source_shape": list(array.shape),
            "source_plane_shape": [source_rows, source_columns],
            "row_indices": row_indices.tolist(),
            "column_indices": column_indices.tolist(),
            "representation": selected_representation,
            "peak": peak,
        }
        return metadata, np.asarray(plane, dtype="<f4")

    def slice(
        self,
        product_id: str,
        permutation: list[int],
        layer: int,
        limit: int = 96,
        indices: dict[int, int] | None = None,
        representation: str | None = None,
    ) -> dict:
        metadata, plane = self._slice_plane(
            product_id, permutation, layer, limit, indices, representation
        )
        metadata["values"] = plane.astype(float).tolist()
        return metadata

    def slice_binary(
        self,
        product_id: str,
        permutation: list[int],
        layer: int,
        limit: int = 96,
        indices: dict[int, int] | None = None,
        representation: str | None = None,
    ) -> tuple[dict, bytes]:
        metadata, plane = self._slice_plane(
            product_id, permutation, layer, limit, indices, representation
        )
        metadata["rows"], metadata["columns"] = plane.shape
        return metadata, plane.tobytes(order="C")

    def volume_binary(
        self,
        product_id: str,
        permutation: list[int],
        limit: int = 96,
        indices: dict[int, int] | None = None,
        depth_limit: int = MAX_VOLUME_DEPTH,
        representation: str | None = None,
        max_values: int = MAX_VOLUME_VALUES,
    ) -> tuple[dict, bytes]:
        """Return a bounded, uniformly sampled context volume for rendering."""
        array = self.array(product_id)
        product = self.products[product_id]
        if not 1 <= len(permutation) <= min(3, array.ndim):
            raise ValueError("Permutation must select between 1 and 3 display axes")
        if len(set(permutation)) != len(permutation) or any(
            axis < 0 or axis >= array.ndim for axis in permutation
        ):
            raise ValueError("Display axes must be unique valid array axes")
        indices = indices or {}
        selectors: list[int | slice] = []
        remaining_axes: list[int] = []
        for axis, size in enumerate(array.shape):
            if axis in permutation:
                selectors.append(slice(None))
                remaining_axes.append(axis)
            else:
                selected = indices.get(axis, 0)
                if not 0 <= selected < size:
                    raise ValueError(
                        f"Index {selected} is out of range for axis {axis}"
                    )
                selectors.append(selected)
        view = np.transpose(
            array[tuple(selectors)],
            [remaining_axes.index(axis) for axis in permutation],
        )
        if len(permutation) == 1:
            view = view[np.newaxis, np.newaxis, :]
        elif len(permutation) == 2:
            view = view[np.newaxis, :, :]
        source_depth, source_rows, source_columns = view.shape
        depth_indices = _uniform_indices(source_depth, depth_limit)
        sampled_depth = len(depth_indices)
        rows, columns = min(source_rows, limit), min(source_columns, limit)
        if (
            isinstance(max_values, bool)
            or not isinstance(max_values, int)
            or max_values < 1
        ):
            raise ValueError("max_values must be a positive integer")
        plane_budget = max(1, max_values // sampled_depth)
        if rows * columns > plane_budget:
            factor = np.sqrt(plane_budget / (rows * columns))
            rows = max(1, int(rows * factor))
            columns = max(1, int(columns * factor))
        row_indices = _uniform_indices(source_rows, rows)
        column_indices = _uniform_indices(source_columns, columns)
        sampled = view[np.ix_(depth_indices, row_indices, column_indices)]
        selected_representation = representation or product.get(
            "representation", "auto"
        )
        if selected_representation not in REPRESENTATIONS:
            raise ValueError(f"Unknown representation: {selected_representation!r}")
        if selected_representation in {"imag", "phase"} and not np.iscomplexobj(array):
            raise ValueError(
                f"Representation {selected_representation!r} requires complex-valued data"
            )
        volume = _float32_display(sampled, selected_representation)
        finite = volume[np.isfinite(volume)]
        metadata = {
            "product": product_id,
            "permutation": permutation,
            "indices": indices,
            "depth": volume.shape[0],
            "source_depth": source_depth,
            "rows": rows,
            "columns": columns,
            "shape": list(volume.shape),
            "source_shape": list(array.shape),
            "source_plane_shape": [source_rows, source_columns],
            "depth_indices": depth_indices.tolist(),
            "row_indices": row_indices.tolist(),
            "column_indices": column_indices.tolist(),
            "representation": selected_representation,
            "peak": float(finite.max()) if finite.size else 0.0,
        }
        return metadata, volume.tobytes(order="C")


def make_handler(store: RunStore):
    web_root = files("spviz").joinpath("web")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")

        def _json(self, payload, status=200):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _binary(self, metadata, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.spviz.float32")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "X-Spviz-Metadata", json.dumps(metadata, separators=(",", ":"))
            )
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                store.refresh()
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                return self._json({"error": f"Unable to refresh run: {error}"}, 500)
            request = urlparse(self.path)
            if request.path == "/api/run":
                return self._json(store.manifest)
            if (
                request.path.startswith("/api/product/")
                and "/coordinates/" in request.path
            ):
                parts = request.path.split("/")
                try:
                    if len(parts) != 6 or parts[4] != "coordinates":
                        raise ValueError("Malformed coordinate URL")
                    query = parse_qs(request.query)
                    limit = min(
                        100_000,
                        max(1, int(query.get("limit", ["4096"])[0])),
                    )
                    return self._json(store.coordinates(parts[3], int(parts[5]), limit))
                except (IndexError, KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            if request.path.startswith("/api/product/") and request.path.endswith(
                "/volume"
            ):
                product_id = request.path.split("/")[3]
                query = parse_qs(request.query)
                try:
                    product = store.products[product_id]
                    default_axes = [
                        product["axes"].index(name)
                        for name in product.get("view_axes", product["axes"][:3])
                    ]
                    permutation = [
                        int(v)
                        for v in query.get("perm", [",".join(map(str, default_axes))])[
                            0
                        ].split(",")
                    ]
                    limit = min(
                        MAX_RENDER_DIMENSION, max(1, int(query.get("limit", ["96"])[0]))
                    )
                    depth_limit = min(
                        MAX_VOLUME_DEPTH,
                        max(1, int(query.get("depth_limit", ["12"])[0])),
                    )
                    indices = {
                        int(axis): int(value)
                        for item in query.get("index", [])
                        for axis, value in [item.split(":", 1)]
                    }
                    representation = query.get("representation", [None])[0]
                    metadata, body = store.volume_binary(
                        product_id,
                        permutation,
                        limit,
                        indices,
                        depth_limit,
                        representation,
                    )
                    return self._binary(metadata, body)
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            if request.path.startswith("/api/product/") and request.path.endswith(
                "/slice"
            ):
                product_id = request.path.split("/")[3]
                query = parse_qs(request.query)
                try:
                    product = store.products[product_id]
                    default_axes = [
                        product["axes"].index(name)
                        for name in product.get("view_axes", product["axes"][:3])
                    ]
                    permutation = [
                        int(v)
                        for v in query.get("perm", [",".join(map(str, default_axes))])[
                            0
                        ].split(",")
                    ]
                    layer = int(query.get("layer", ["0"])[0])
                    limit = min(
                        MAX_RENDER_DIMENSION, max(1, int(query.get("limit", ["96"])[0]))
                    )
                    indices = {}
                    for item in query.get("index", []):
                        axis, value = item.split(":", 1)
                        indices[int(axis)] = int(value)
                    representation = query.get("representation", [None])[0]
                    if query.get("format", [""])[0] == "f32":
                        metadata, body = store.slice_binary(
                            product_id,
                            permutation,
                            layer,
                            limit=limit,
                            indices=indices,
                            representation=representation,
                        )
                        return self._binary(metadata, body)
                    return self._json(
                        store.slice(
                            product_id,
                            permutation,
                            layer,
                            limit=limit,
                            indices=indices,
                            representation=representation,
                        )
                    )
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            asset = "index.html" if request.path == "/" else request.path.lstrip("/")
            if asset == "config.js":
                body = b"window.SPVIZ_STATIC_BASE = null;\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                self.wfile.write(body)
                return
            if asset not in WEB_ASSETS:
                self.send_error(404)
                return
            candidate = web_root.joinpath(asset)
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(asset)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            if asset == "index.html":
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return Handler


def serve(run_dir: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = RunStore(run_dir)
    server = ThreadingHTTPServer((host, port), make_handler(store))
    server.daemon_threads = True
    print(f"spviz: serving {store.path}")
    print(f"spviz: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
