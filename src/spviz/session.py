from __future__ import annotations

import contextvars
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

_active: contextvars.ContextVar["Session | None"] = contextvars.ContextVar("spviz_session", default=None)


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return value or "product"


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class Session:
    """Record arrays and their lineage into a portable spviz run directory."""

    def __init__(self, path: str | Path, *, name: str = "Signal-processing run", metadata: dict[str, Any] | None = None):
        self.path = Path(path).expanduser().resolve()
        self.name = name
        self.metadata = metadata or {}
        self.products: list[dict[str, Any]] = []
        self._token: contextvars.Token | None = None
        self._started = time.time()

    def __enter__(self) -> "Session":
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "arrays").mkdir(exist_ok=True)
        self._token = _active.set(self)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        if self._token is not None:
            _active.reset(self._token)

    def capture(
        self,
        name: str,
        value: Any,
        *,
        axes: Iterable[str] | None = None,
        view_axes: Iterable[str | int] | None = None,
        coordinates: dict[str | int, Any] | None = None,
        filename: str | Path | None = None,
        scale: str = "linear",
        vmin: float | None = None,
        vmax: float | None = None,
        operation: str | None = None,
        upstream: str | Iterable[str] | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        array = np.asarray(value)
        if array.ndim == 0:
            array = array.reshape(1)
        axis_names = list(axes or (f"axis_{i}" for i in range(array.ndim)))
        if scale not in {"linear", "log"}:
            raise ValueError("scale must be 'linear' or 'log'")
        if vmin is not None and not np.isfinite(vmin):
            raise ValueError("vmin must be finite")
        if vmax is not None and not np.isfinite(vmax):
            raise ValueError("vmax must be finite")
        if vmin is not None and vmax is not None and vmin >= vmax:
            raise ValueError("vmin must be less than vmax")
        if len(axis_names) != array.ndim:
            raise ValueError(f"Expected {array.ndim} axis names, received {len(axis_names)}")
        if len(set(axis_names)) != len(axis_names):
            raise ValueError("Axis names must be unique")
        if view_axes is None:
            if array.ndim > 3:
                raise ValueError("Arrays with more than 3 dimensions require view_axes")
            resolved_view_axes = list(range(array.ndim))
        else:
            requested = list(view_axes)
            if array.ndim > 3 and len(requested) != 3:
                raise ValueError("Arrays with more than 3 dimensions require exactly 3 view_axes")
            if not 1 <= len(requested) <= min(3, array.ndim):
                raise ValueError("view_axes must select between 1 and 3 dimensions")
            resolved_view_axes = []
            for axis in requested:
                if isinstance(axis, str):
                    if axis not in axis_names:
                        raise ValueError(f"Unknown view axis: {axis}")
                    resolved_view_axes.append(axis_names.index(axis))
                else:
                    index = int(axis)
                    if index < 0:
                        index += array.ndim
                    if not 0 <= index < array.ndim:
                        raise ValueError(f"View axis out of range: {axis}")
                    resolved_view_axes.append(index)
            if len(set(resolved_view_axes)) != len(resolved_view_axes):
                raise ValueError("view_axes cannot contain duplicates")
        product_id = _slug(name)
        existing = {p["id"] for p in self.products}
        base, suffix = product_id, 2
        while product_id in existing:
            product_id = f"{base}-{suffix}"
            suffix += 1
        if filename is None:
            array_filename = f"{product_id}.npy"
        else:
            requested_filename = Path(filename)
            if requested_filename.name != str(requested_filename) or requested_filename.name in {"", ".", ".."}:
                raise ValueError("filename must be a file name, not a path")
            array_filename = requested_filename.name
            if Path(array_filename).suffix == "":
                array_filename += ".npy"
            elif Path(array_filename).suffix != ".npy":
                raise ValueError("filename must use the .npy extension")
            used_files = {Path(product["file"]).name for product in self.products}
            if array_filename in used_files:
                raise ValueError(f"filename is already used in this run: {array_filename}")
        stored_filename = f"arrays/{array_filename}"
        np.save(self.path / stored_filename, array, allow_pickle=False)
        coordinate_manifest: dict[str, dict[str, Any]] = {}
        for axis_key, specification in (coordinates or {}).items():
            if isinstance(axis_key, str):
                if axis_key not in axis_names:
                    raise ValueError(f"Unknown coordinate axis: {axis_key}")
                axis_index = axis_names.index(axis_key)
            else:
                axis_index = int(axis_key)
                if axis_index < 0:
                    axis_index += array.ndim
                if not 0 <= axis_index < array.ndim:
                    raise ValueError(f"Coordinate axis out of range: {axis_key}")
            if isinstance(specification, dict) and "values" in specification:
                coordinate_values = specification["values"]
                coordinate_units = specification.get("units")
            else:
                coordinate_values = specification
                coordinate_units = None
            coordinate_array = np.asarray(coordinate_values)
            if coordinate_array.ndim != 1 or len(coordinate_array) != array.shape[axis_index]:
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} must be one-dimensional with length {array.shape[axis_index]}"
                )
            if np.iscomplexobj(coordinate_array):
                raise ValueError(f"Coordinates for {axis_names[axis_index]} cannot be complex")
            if coordinate_array.dtype.hasobject:
                coordinate_array = coordinate_array.astype(str)
            coordinate_file = f"arrays/{product_id}--axis-{axis_index}.npy"
            np.save(self.path / coordinate_file, coordinate_array, allow_pickle=False)
            coordinate_manifest[axis_names[axis_index]] = {
                "file": coordinate_file,
                "dtype": str(coordinate_array.dtype),
                "length": len(coordinate_array),
                "units": coordinate_units,
            }
        if upstream is None:
            parents: list[str] = []
        elif isinstance(upstream, str):
            parents = [upstream]
        else:
            parents = list(upstream)
        magnitude = np.abs(array)
        finite = magnitude[np.isfinite(magnitude)]
        stats = {
            "min": float(finite.min()) if finite.size else None,
            "max": float(finite.max()) if finite.size else None,
            "mean": float(finite.mean()) if finite.size else None,
            "value_min": float(array[np.isfinite(array)].min()) if finite.size and not np.iscomplexobj(array) else None,
            "value_max": float(array[np.isfinite(array)].max()) if finite.size and not np.iscomplexobj(array) else None,
        }
        self.products.append({
            "id": product_id,
            "name": name,
            "file": stored_filename,
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "scale": scale,
            "display_min": float(vmin) if vmin is not None else None,
            "display_max": float(vmax) if vmax is not None else None,
            "bytes": int(array.nbytes),
            "axes": axis_names,
            "view_axes": [axis_names[index] for index in resolved_view_axes],
            "coordinates": coordinate_manifest,
            "operation": operation,
            "upstream": parents,
            "units": units,
            "metadata": _jsonable(metadata or {}),
            "stats": stats,
        })
        return product_id

    def close(self) -> Path:
        manifest = {
            "format": "spviz-run",
            "version": 1,
            "name": self.name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._started)),
            "metadata": _jsonable(self.metadata),
            "products": self.products,
        }
        target = self.path / "manifest.json"
        target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return target


def current_session() -> Session:
    session = _active.get()
    if session is None:
        raise RuntimeError("spviz.capture() must be called inside `with spviz.Session(...)`")
    return session


def capture(name: str, value: Any, **kwargs: Any) -> str:
    return current_session().capture(name, value, **kwargs)
