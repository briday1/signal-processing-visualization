from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np


class RunStore:
    def __init__(self, run_dir: str | Path):
        self.path = Path(run_dir).expanduser().resolve()
        manifest_path = self.path / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Not an spviz run: {manifest_path} is missing")
        self._manifest_path = manifest_path
        self._manifest_mtime_ns = -1
        self.refresh()

    def refresh(self) -> None:
        """Reload a run that was regenerated while its viewer stays open."""
        mtime_ns = self._manifest_path.stat().st_mtime_ns
        if mtime_ns == self._manifest_mtime_ns:
            return
        self.manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        self.products = {p["id"]: p for p in self.manifest["products"]}
        self._manifest_mtime_ns = mtime_ns

    def array(self, product_id: str) -> np.ndarray:
        product = self.products.get(product_id)
        if product is None:
            raise KeyError(product_id)
        return np.load(self.path / product["file"], mmap_mode="r", allow_pickle=False)

    def coordinates(self, product_id: str, axis: int) -> dict:
        product = self.products.get(product_id)
        if product is None or not 0 <= axis < len(product["axes"]):
            raise KeyError(product_id)
        axis_name = product["axes"][axis]
        descriptor = product.get("coordinates", {}).get(axis_name)
        if descriptor is None:
            values = list(range(product["shape"][axis]))
            return {"axis": axis, "name": axis_name, "units": None, "values": values, "implicit": True}
        array = np.load(self.path / descriptor["file"], allow_pickle=False)
        if array.dtype.kind in "mM":
            values = np.datetime_as_string(array).tolist()
        else:
            values = array.tolist()
        return {"axis": axis, "name": axis_name, "units": descriptor.get("units"), "values": values, "implicit": False}

    def _slice_plane(
        self,
        product_id: str,
        permutation: list[int],
        layer: int,
        limit: int = 96,
        indices: dict[int, int] | None = None,
    ) -> tuple[dict, np.ndarray]:
        array = self.array(product_id)
        if not 1 <= len(permutation) <= min(3, array.ndim):
            raise ValueError("Permutation must select between 1 and 3 display axes")
        if len(set(permutation)) != len(permutation) or any(axis < 0 or axis >= array.ndim for axis in permutation):
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
                    raise ValueError(f"Index {selected} is out of range for axis {axis}")
                selectors.append(selected)
        view = array[tuple(selectors)]
        view = np.transpose(view, [remaining_axes.index(axis) for axis in permutation])
        if len(permutation) == 1:
            view = view[np.newaxis, np.newaxis, :]
        elif len(permutation) == 2:
            view = view[np.newaxis, :, :]
        depth = view.shape[0]
        layer = max(0, min(layer, depth - 1))
        plane = np.abs(view[layer])
        source_rows, source_columns = plane.shape
        target_rows = min(source_rows, limit)
        target_columns = min(source_columns, limit)
        row_indices = np.rint(np.linspace(0, source_rows - 1, target_rows)).astype(int)
        column_indices = np.rint(np.linspace(0, source_columns - 1, target_columns)).astype(int)
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
            "source_shape": list(self.array(product_id).shape),
            "source_plane_shape": [source_rows, source_columns],
            "row_step": source_rows / target_rows,
            "column_step": source_columns / target_columns,
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
    ) -> dict:
        metadata, plane = self._slice_plane(product_id, permutation, layer, limit, indices)
        metadata["values"] = plane.astype(float).tolist()
        return metadata

    def slice_binary(
        self,
        product_id: str,
        permutation: list[int],
        layer: int,
        limit: int = 96,
        indices: dict[int, int] | None = None,
    ) -> tuple[dict, bytes]:
        metadata, plane = self._slice_plane(product_id, permutation, layer, limit, indices)
        metadata["rows"], metadata["columns"] = plane.shape
        return metadata, plane.tobytes(order="C")

    def volume_binary(
        self,
        product_id: str,
        permutation: list[int],
        limit: int = 96,
        indices: dict[int, int] | None = None,
    ) -> tuple[dict, bytes]:
        """Return every display layer in one transfer for interactive rendering."""
        array = self.array(product_id)
        if not 1 <= len(permutation) <= min(3, array.ndim):
            raise ValueError("Permutation must select between 1 and 3 display axes")
        if len(set(permutation)) != len(permutation) or any(axis < 0 or axis >= array.ndim for axis in permutation):
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
                    raise ValueError(f"Index {selected} is out of range for axis {axis}")
                selectors.append(selected)
        view = np.transpose(array[tuple(selectors)], [remaining_axes.index(axis) for axis in permutation])
        if len(permutation) == 1:
            view = view[np.newaxis, np.newaxis, :]
        elif len(permutation) == 2:
            view = view[np.newaxis, :, :]
        source_rows, source_columns = view.shape[-2:]
        rows, columns = min(source_rows, limit), min(source_columns, limit)
        row_indices = np.rint(np.linspace(0, source_rows - 1, rows)).astype(int)
        column_indices = np.rint(np.linspace(0, source_columns - 1, columns)).astype(int)
        volume = np.asarray(np.abs(view[:, row_indices][:, :, column_indices]), dtype="<f4")
        finite = volume[np.isfinite(volume)]
        metadata = {
            "product": product_id,
            "permutation": permutation,
            "indices": indices,
            "depth": volume.shape[0],
            "rows": rows,
            "columns": columns,
            "shape": list(volume.shape),
            "source_shape": list(array.shape),
            "source_plane_shape": [source_rows, source_columns],
            "peak": float(finite.max()) if finite.size else 0.0,
        }
        return metadata, volume.tobytes(order="C")


def make_handler(store: RunStore):
    web_root = files("spviz").joinpath("web")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload, status=200):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _binary(self, metadata, body):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.spviz.float32")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Spviz-Metadata", json.dumps(metadata, separators=(",", ":")))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            store.refresh()
            request = urlparse(self.path)
            if request.path == "/api/run":
                return self._json(store.manifest)
            if request.path.startswith("/api/product/") and "/coordinates/" in request.path:
                parts = request.path.split("/")
                try:
                    return self._json(store.coordinates(parts[3], int(parts[5])))
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            if request.path.startswith("/api/product/") and request.path.endswith("/volume"):
                product_id = request.path.split("/")[3]
                query = parse_qs(request.query)
                try:
                    product = store.products[product_id]
                    default_axes = [product["axes"].index(name) for name in product.get("view_axes", product["axes"][:3])]
                    permutation = [int(v) for v in query.get("perm", [",".join(map(str, default_axes))])[0].split(",")]
                    limit = max(1, int(query.get("limit", ["96"])[0]))
                    indices = {int(axis): int(value) for item in query.get("index", []) for axis, value in [item.split(":", 1)]}
                    metadata, body = store.volume_binary(product_id, permutation, limit, indices)
                    return self._binary(metadata, body)
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            if request.path.startswith("/api/product/") and request.path.endswith("/slice"):
                product_id = request.path.split("/")[3]
                query = parse_qs(request.query)
                try:
                    array = store.array(product_id)
                    product = store.products[product_id]
                    default_axes = [product["axes"].index(name) for name in product.get("view_axes", product["axes"][:3])]
                    permutation = [int(v) for v in query.get("perm", [",".join(map(str, default_axes))])[0].split(",")]
                    layer = int(query.get("layer", ["0"])[0])
                    limit = max(1, int(query.get("limit", ["96"])[0]))
                    indices = {}
                    for item in query.get("index", []):
                        axis, value = item.split(":", 1)
                        indices[int(axis)] = int(value)
                    if query.get("format", [""])[0] == "f32":
                        metadata, body = store.slice_binary(product_id, permutation, layer, limit=limit, indices=indices)
                        return self._binary(metadata, body)
                    return self._json(store.slice(product_id, permutation, layer, limit=limit, indices=indices))
                except (KeyError, ValueError) as error:
                    return self._json({"error": str(error)}, 400)
            asset = "index.html" if request.path == "/" else request.path.lstrip("/")
            if asset == "config.js":
                body = b"window.SPVIZ_STATIC_BASE = null;\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            candidate = web_root.joinpath(asset)
            if not candidate.is_file():
                self.send_error(404)
                return
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(asset)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return Handler


def serve(run_dir: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = RunStore(run_dir)
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"spviz: serving {store.path}")
    print(f"spviz: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
