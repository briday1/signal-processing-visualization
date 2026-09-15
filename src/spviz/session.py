from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable
from numbers import Integral
from pathlib import Path
from typing import Any, Literal, TypeAlias

import numpy as np

_active: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    "spviz_session", default=None
)
_publish_locks_guard = threading.Lock()
_publish_locks: dict[Path, threading.Lock] = {}
Representation: TypeAlias = Literal[
    "auto", "real", "imag", "magnitude", "power", "phase"
]
Scale: TypeAlias = Literal["linear", "log"]
Aspect: TypeAlias = Literal["data", "equal", "fit"]
WriteMode: TypeAlias = Literal["replace", "error"]
Statistics: TypeAlias = Literal["exact", "sampled", "none"]
REPRESENTATIONS = {"auto", "real", "imag", "magnitude", "power", "phase"}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    # Keep generated array names comfortably below common 255-byte filesystem
    # limits, including temporary-file suffixes and coordinate descriptors.
    return value[:96].rstrip("-_") or "product"


def _jsonable(value: Any, active: set[int] | None = None) -> Any:
    active = set() if active is None else active
    if isinstance(value, np.generic):
        return _jsonable(value.item(), active)
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist(), active)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("metadata cannot contain recursive containers")
        active.add(identity)
        try:
            if isinstance(value, dict):
                return {str(k): _jsonable(v, active) for k, v in value.items()}
            return [_jsonable(v, active) for v in value]
        finally:
            active.remove(identity)
    return value


def _validated_json(value: Any, label: str) -> Any:
    """Return a JSON-compatible copy or fail before any run files are written."""
    try:
        converted = _jsonable(value)
        json.dumps(converted, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{label} must contain only finite JSON-serializable values"
        ) from error
    return converted


def _atomic_save(target: Path, array: np.ndarray) -> None:
    """Write an NPY file without ever exposing a partially written target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            np.save(stream, array, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _run_signature(path: Path) -> str | None:
    """Fingerprint a replaceable run directory without trusting its manifest."""
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Session path must be a real directory")
    entries = list(path.iterdir())
    if not entries:
        return "empty"
    manifest = path / "manifest.json"
    arrays = path / "arrays"
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or arrays.is_symlink()
        or not arrays.is_dir()
    ):
        raise ValueError(
            "Refusing to replace a non-empty directory that is not an spviz run"
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Refusing to replace an invalid spviz run") from error
    if payload.get("format") != "spviz-run":
        raise ValueError("Refusing to replace a non-spviz directory")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _replace_directory(staged: Path, target: Path) -> None:
    """Atomically publish a staged run and restore the old run on swap failure."""
    if not target.exists():
        os.replace(staged, target)
        return
    backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup-", dir=target.parent))
    backup.rmdir()
    os.replace(target, backup)
    try:
        os.replace(staged, target)
    except BaseException:
        os.replace(backup, target)
        raise
    try:
        shutil.rmtree(backup)
    except OSError:
        # Publication already committed. A cleanup failure must not make callers
        # retry a successful close or corrupt the Session state.
        pass


def _publish_lock(path: Path) -> threading.Lock:
    with _publish_locks_guard:
        return _publish_locks.setdefault(path, threading.Lock())


def _generated_filename(candidate: str, reserved: set[str]) -> str:
    """Choose a deterministic free filename for an internally named artifact."""
    path = Path(candidate)
    result = candidate
    suffix = 2
    while result.casefold() in reserved:
        result = f"{path.stem}-{suffix}{path.suffix}"
        suffix += 1
    reserved.add(result.casefold())
    return result


def _display_values(array: np.ndarray, representation: Representation) -> np.ndarray:
    """Return the scalar values represented by a captured real or complex array."""
    resolved = (
        "magnitude"
        if representation == "auto" and np.iscomplexobj(array)
        else representation
    )
    if resolved == "auto" or resolved == "real":
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
    return np.angle(array)


def _statistics_source(array: np.ndarray, mode: Statistics) -> np.ndarray:
    if mode == "exact" or array.size <= 1_000_000:
        return array
    low, high = 1, max(array.shape)
    while low < high:
        candidate = (low + high + 1) // 2
        count = 1
        for size in array.shape:
            count *= min(size, candidate)
            if count > 1_000_000:
                break
        if count <= 1_000_000:
            low = candidate
        else:
            high = candidate - 1
    per_axis = low
    indices = [
        np.rint(np.linspace(0, size - 1, min(size, per_axis))).astype(np.intp)
        for size in array.shape
    ]
    return array[np.ix_(*indices)]


def _stable_mean(values: np.ndarray) -> float:
    """Compute a float64 mean without overflowing sums or losing subnormals."""
    converted = np.asarray(values, dtype=np.float64)
    scale = float(np.max(np.abs(converted)))
    if scale == 0:
        return 0.0
    return float(np.sum(converted / scale, dtype=np.float64) / converted.size * scale)


def _array_statistics(
    array: np.ndarray, representation: Representation, mode: Statistics
) -> dict[str, float | None]:
    """Compute manifest statistics in bounded memory for arbitrarily large arrays."""
    if mode == "none":
        return {
            key: None
            for key in (
                "min",
                "max",
                "mean",
                "value_min",
                "value_max",
                "display_min",
                "display_max",
                "display_mean",
            )
        }
    source = _statistics_source(array, mode)
    magnitude_min = np.inf
    magnitude_max = -np.inf
    magnitude_mean = 0.0
    magnitude_count = 0
    display_min = np.inf
    display_max = -np.inf
    display_mean = 0.0
    display_count = 0
    value_min = np.inf
    value_max = -np.inf

    iterator = np.nditer(
        source,
        flags=["external_loop", "buffered", "zerosize_ok"],
        op_flags=["readonly"],
        order="K",
        buffersize=1_048_576,
    )
    for chunk in iterator:
        magnitude_source = (
            chunk.astype(np.float64, copy=False) if chunk.dtype.kind in "iu" else chunk
        )
        magnitude = np.abs(magnitude_source)
        finite_magnitude = magnitude[np.isfinite(magnitude)]
        if finite_magnitude.size:
            magnitude_min = min(magnitude_min, float(finite_magnitude.min()))
            magnitude_max = max(magnitude_max, float(finite_magnitude.max()))
            chunk_count = int(finite_magnitude.size)
            new_count = magnitude_count + chunk_count
            chunk_mean = _stable_mean(finite_magnitude)
            magnitude_mean = magnitude_mean * (
                magnitude_count / new_count
            ) + chunk_mean * (chunk_count / new_count)
            magnitude_count = new_count

        displayed = _display_values(chunk, representation)
        generated_invalid = np.isfinite(chunk) & ~np.isfinite(displayed)
        if np.any(generated_invalid):
            raise ValueError(
                f"representation={representation!r} overflows for the captured values"
            )
        finite_display = displayed[np.isfinite(displayed)]
        if finite_display.size:
            display_min = min(display_min, float(finite_display.min()))
            display_max = max(display_max, float(finite_display.max()))
            chunk_count = int(finite_display.size)
            new_count = display_count + chunk_count
            chunk_mean = _stable_mean(finite_display)
            display_mean = display_mean * (display_count / new_count) + chunk_mean * (
                chunk_count / new_count
            )
            display_count = new_count

        if not np.iscomplexobj(array):
            finite_values = chunk[np.isfinite(chunk)]
            if finite_values.size:
                value_min = min(value_min, float(finite_values.min()))
                value_max = max(value_max, float(finite_values.max()))

    return {
        "min": magnitude_min if magnitude_count else None,
        "max": magnitude_max if magnitude_count else None,
        "mean": magnitude_mean if magnitude_count else None,
        "value_min": value_min
        if magnitude_count and not np.iscomplexobj(array)
        else None,
        "value_max": value_max
        if magnitude_count and not np.iscomplexobj(array)
        else None,
        "display_min": display_min if display_count else None,
        "display_max": display_max if display_count else None,
        "display_mean": display_mean if display_count else None,
    }


class Session:
    """Record arrays and their lineage into a portable spviz run directory."""

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "Signal-processing run",
        metadata: dict[str, Any] | None = None,
        mode: WriteMode = "replace",
    ):
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not name.strip():
            raise ValueError("name cannot be empty")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("session metadata must be a dictionary")
        if mode not in {"replace", "error"}:
            raise ValueError("mode must be 'replace' or 'error'")
        self.path = Path(path).expanduser().resolve()
        if self.path == Path(self.path.anchor):
            raise ValueError("Session path cannot be a filesystem root")
        self.name = name
        self.mode = mode
        self.metadata = _validated_json(
            {} if metadata is None else metadata, "session metadata"
        )
        self.products: list[dict[str, Any]] = []
        self._token: contextvars.Token | None = None
        self._started = time.time()
        self._closed = False
        self._lock = threading.RLock()
        self._working_path: Path | None = None
        self._initial_signature = _run_signature(self.path)
        if mode == "error" and self._initial_signature is not None:
            raise FileExistsError(f"Session path already exists: {self.path}")

    def _prepare_directory(self) -> None:
        if self._working_path is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _run_signature(self.path) != self._initial_signature:
            raise RuntimeError("Session path changed after this recorder was created")
        self._working_path = Path(
            tempfile.mkdtemp(prefix=f".{self.path.name}.capture-", dir=self.path.parent)
        )
        (self._working_path / "arrays").mkdir()

    @property
    def _storage_path(self) -> Path:
        self._prepare_directory()
        assert self._working_path is not None
        return self._working_path

    def abort(self) -> None:
        """Discard an unfinished staged run, leaving any previous run untouched."""
        with self._lock:
            if self._working_path is not None:
                shutil.rmtree(self._working_path)
                self._working_path = None
            self._closed = True

    def __enter__(self) -> Session:
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot re-enter a closed session")
            if self._token is not None:
                raise RuntimeError("Session is already active in this context")
            self._prepare_directory()
            self._token = _active.set(self)
            return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                try:
                    self.close()
                except BaseException:
                    self.abort()
                    raise
            else:
                self.abort()
        finally:
            if self._token is not None:
                _active.reset(self._token)
                self._token = None

    def capture(
        self,
        name: str,
        value: Any,
        *,
        axes: Iterable[str] | None = None,
        view_axes: Iterable[str | int] | None = None,
        coordinates: dict[str | int, Any] | None = None,
        filename: str | Path | None = None,
        views: dict[str, dict[str, Any]] | None = None,
        primary_view: str | None = None,
        representation: Representation = "auto",
        statistics: Statistics = "exact",
        scale: Scale = "linear",
        overview_aspect: Aspect | None = None,
        vmin: float | None = None,
        vmax: float | None = None,
        operation: str | None = None,
        upstream: str | Iterable[str] | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot capture values after the session is closed")
            self._prepare_directory()
            return self._capture_unlocked(
                name,
                value,
                axes=axes,
                view_axes=view_axes,
                coordinates=coordinates,
                filename=filename,
                views=views,
                primary_view=primary_view,
                representation=representation,
                statistics=statistics,
                scale=scale,
                overview_aspect=overview_aspect,
                vmin=vmin,
                vmax=vmax,
                operation=operation,
                upstream=upstream,
                units=units,
                metadata=metadata,
            )

    def _capture_unlocked(
        self,
        name: str,
        value: Any,
        *,
        axes: Iterable[str] | None = None,
        view_axes: Iterable[str | int] | None = None,
        coordinates: dict[str | int, Any] | None = None,
        filename: str | Path | None = None,
        views: dict[str, dict[str, Any]] | None = None,
        primary_view: str | None = None,
        representation: Representation = "auto",
        statistics: Statistics = "exact",
        scale: Scale = "linear",
        overview_aspect: Aspect | None = None,
        vmin: float | None = None,
        vmax: float | None = None,
        operation: str | None = None,
        upstream: str | Iterable[str] | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not name.strip():
            raise ValueError("name cannot be empty")
        if operation is not None and not isinstance(operation, str):
            raise TypeError("operation must be a string or None")
        if units is not None and not isinstance(units, str):
            raise TypeError("units must be a string or None")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("product metadata must be a dictionary")
        product_metadata = _validated_json(
            {} if metadata is None else metadata, "product metadata"
        )

        array = np.asarray(value)
        if array.ndim == 0:
            array = array.reshape(1)
        if array.size == 0:
            raise ValueError("Captured arrays cannot be empty")
        if array.dtype.kind not in "buifc":
            raise ValueError(
                f"Captured arrays must contain numeric or boolean data, received {array.dtype}"
            )
        axis_names = list(
            (f"axis_{i}" for i in range(array.ndim)) if axes is None else axes
        )
        if any(not isinstance(axis, str) or not axis for axis in axis_names):
            raise ValueError("Axis names must be non-empty strings")
        if scale not in {"linear", "log"}:
            raise ValueError("scale must be 'linear' or 'log'")
        if representation not in REPRESENTATIONS:
            choices = ", ".join(sorted(REPRESENTATIONS))
            raise ValueError(f"representation must be one of: {choices}")
        if representation in {"imag", "phase"} and not np.iscomplexobj(array):
            raise ValueError(
                f"representation={representation!r} requires complex-valued data"
            )
        if statistics not in {"exact", "sampled", "none"}:
            raise ValueError("statistics must be 'exact', 'sampled', or 'none'")
        if overview_aspect not in {None, "data", "equal", "fit"}:
            raise ValueError("overview_aspect must be 'data', 'equal', or 'fit'")
        if vmin is not None:
            try:
                vmin = float(vmin)
            except (TypeError, ValueError) as error:
                raise ValueError("vmin must be finite") from error
            if not np.isfinite(vmin):
                raise ValueError("vmin must be finite")
        if vmax is not None:
            try:
                vmax = float(vmax)
            except (TypeError, ValueError) as error:
                raise ValueError("vmax must be finite") from error
            if not np.isfinite(vmax):
                raise ValueError("vmax must be finite")
        if vmin is not None and vmax is not None and vmin >= vmax:
            raise ValueError("vmin must be less than vmax")
        if statistics == "none" and (vmin is None or vmax is None):
            raise ValueError(
                "statistics='none' requires both vmin and vmax for a usable display range"
            )
        if len(axis_names) != array.ndim:
            raise ValueError(
                f"Expected {array.ndim} axis names, received {len(axis_names)}"
            )
        if len(set(axis_names)) != len(axis_names):
            raise ValueError("Axis names must be unique")
        if view_axes is None:
            if array.ndim > 3:
                raise ValueError("Arrays with more than 3 dimensions require view_axes")
            resolved_view_axes = list(range(array.ndim))
        else:
            requested = list(view_axes)
            if array.ndim > 3 and len(requested) != 3:
                raise ValueError(
                    "Arrays with more than 3 dimensions require exactly 3 view_axes"
                )
            if not 1 <= len(requested) <= min(3, array.ndim):
                raise ValueError("view_axes must select between 1 and 3 dimensions")
            resolved_view_axes = []
            for axis in requested:
                if isinstance(axis, str):
                    if axis not in axis_names:
                        raise ValueError(f"Unknown view axis: {axis}")
                    resolved_view_axes.append(axis_names.index(axis))
                else:
                    if isinstance(axis, bool) or not isinstance(axis, Integral):
                        raise TypeError(
                            "view_axes entries must be axis names or integers"
                        )
                    index = int(axis)
                    if index < 0:
                        index += array.ndim
                    if not 0 <= index < array.ndim:
                        raise ValueError(f"View axis out of range: {axis}")
                    resolved_view_axes.append(index)
            if len(set(resolved_view_axes)) != len(resolved_view_axes):
                raise ValueError("view_axes cannot contain duplicates")

        product_id = _slug(name)
        existing = {product["id"] for product in self.products}
        base, suffix = product_id, 2
        while product_id in existing:
            product_id = f"{base}-{suffix}"
            suffix += 1

        if upstream is None:
            requested_parents: list[Any] = []
        elif isinstance(upstream, str):
            requested_parents = [upstream]
        else:
            requested_parents = list(upstream)
        parents: list[str] = []
        for parent in requested_parents:
            if not isinstance(parent, str):
                raise TypeError("upstream entries must be product IDs")
            if parent not in existing:
                raise ValueError(f"Unknown upstream product: {parent}")
            if parent not in parents:
                parents.append(parent)

        reserved = {
            Path(descriptor["file"]).name.casefold()
            for product in self.products
            for descriptor in [product, *product.get("coordinates", {}).values()]
        }
        arrays_directory = self._storage_path / "arrays"
        reserved.update(
            candidate.name.casefold()
            for candidate in arrays_directory.iterdir()
            if candidate.is_file()
        )
        if filename is None:
            array_filename = _generated_filename(f"{product_id}.npy", reserved)
        else:
            if not isinstance(filename, (str, Path)):
                raise TypeError("filename must be a string or Path")
            filename_text = str(filename)
            requested_filename = Path(filename)
            if (
                requested_filename.name != filename_text
                or requested_filename.name in {"", ".", ".."}
                or "/" in filename_text
                or "\\" in filename_text
                or "\0" in filename_text
            ):
                raise ValueError("filename must be a file name, not a path")
            array_filename = requested_filename.name
            if Path(array_filename).suffix == "":
                array_filename += ".npy"
            elif Path(array_filename).suffix.lower() != ".npy":
                raise ValueError("filename must use the .npy extension")
            if array_filename.casefold() in reserved:
                raise ValueError(
                    f"filename is already used in this run: {array_filename}"
                )
            if len(array_filename.encode("utf-8")) > 200:
                raise ValueError("filename is too long")
            reserved.add(array_filename.casefold())
        stored_filename = f"arrays/{array_filename}"

        if coordinates is not None and not isinstance(coordinates, dict):
            raise TypeError("coordinates must be a dictionary")
        coordinate_manifest: dict[str, dict[str, Any]] = {}
        coordinate_arrays: list[tuple[str, np.ndarray]] = []
        resolved_coordinate_axes: set[int] = set()
        for axis_key, specification in (coordinates or {}).items():
            if isinstance(axis_key, str):
                if axis_key not in axis_names:
                    raise ValueError(f"Unknown coordinate axis: {axis_key}")
                axis_index = axis_names.index(axis_key)
            else:
                if isinstance(axis_key, bool) or not isinstance(axis_key, Integral):
                    raise TypeError("coordinate keys must be axis names or integers")
                axis_index = int(axis_key)
                if axis_index < 0:
                    axis_index += array.ndim
                if not 0 <= axis_index < array.ndim:
                    raise ValueError(f"Coordinate axis out of range: {axis_key}")
            if axis_index in resolved_coordinate_axes:
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} were specified more than once"
                )
            resolved_coordinate_axes.add(axis_index)
            if isinstance(specification, dict) and "values" in specification:
                coordinate_values = specification["values"]
                coordinate_units = specification.get("units")
            else:
                coordinate_values = specification
                coordinate_units = None
            coordinate_array = np.asarray(coordinate_values)
            if (
                coordinate_array.ndim != 1
                or len(coordinate_array) != array.shape[axis_index]
            ):
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} must be one-dimensional with length {array.shape[axis_index]}"
                )
            if np.iscomplexobj(coordinate_array):
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} cannot be complex"
                )
            if coordinate_units is not None and not isinstance(coordinate_units, str):
                raise ValueError(
                    f"Coordinate units for {axis_names[axis_index]} must be a string or None"
                )
            if coordinate_array.dtype.hasobject or coordinate_array.dtype.kind == "S":
                coordinate_array = coordinate_array.astype(str)
            if coordinate_array.dtype.kind not in "biufUmM":
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} must contain scalar labels or real numeric values"
                )
            if coordinate_array.dtype.kind == "f" and not np.all(
                np.isfinite(coordinate_array)
            ):
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} must be finite"
                )
            if coordinate_array.dtype.kind in "mM" and np.any(
                np.isnat(coordinate_array)
            ):
                raise ValueError(
                    f"Coordinates for {axis_names[axis_index]} cannot contain NaT"
                )
            coordinate_name = _generated_filename(
                f"{product_id}--axis-{axis_index}.npy", reserved
            )
            coordinate_file = f"arrays/{coordinate_name}"
            coordinate_manifest[axis_names[axis_index]] = {
                "file": coordinate_file,
                "dtype": str(coordinate_array.dtype),
                "length": len(coordinate_array),
                "units": coordinate_units,
            }
            coordinate_arrays.append((coordinate_file, coordinate_array))

        stats = _array_statistics(array, representation, statistics)
        product = {
            "id": product_id,
            "name": name,
            "file": stored_filename,
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "representation": representation,
            "statistics": statistics,
            "scale": scale,
            "overview_aspect": overview_aspect,
            "display_min": float(vmin) if vmin is not None else None,
            "display_max": float(vmax) if vmax is not None else None,
            "bytes": int(array.nbytes),
            "axes": axis_names,
            "view_axes": [axis_names[index] for index in resolved_view_axes],
            "coordinates": coordinate_manifest,
            "operation": operation,
            "upstream": parents,
            "units": units,
            "metadata": product_metadata,
            "stats": stats,
        }
        if primary_view is not None and views is None:
            raise ValueError("primary_view requires named views")
        if views is not None:
            product["primary_view"] = primary_view
            from .views import resolve_views

            configurations = resolve_views(views, product)
            product["views"] = _validated_json(views, "views")
            product["view_stats"] = {
                view["view_name"]: _array_statistics(
                    array, view["representation"], statistics
                )
                for view in configurations
            }
        _validated_json(product, "product manifest")

        # All validation and serialization checks above deliberately precede I/O.
        written: list[Path] = []
        try:
            array_target = self._storage_path / stored_filename
            _atomic_save(array_target, array)
            written.append(array_target)
            for coordinate_file, coordinate_array in coordinate_arrays:
                coordinate_target = self._storage_path / coordinate_file
                _atomic_save(coordinate_target, coordinate_array)
                written.append(coordinate_target)
        except OSError:
            for target in written:
                target.unlink(missing_ok=True)
            raise
        self.products.append(product)
        return product_id

    def close(self) -> Path:
        with self._lock:
            target = self.path / "manifest.json"
            if self._closed:
                return target
            working = self._storage_path
            if not isinstance(self.metadata, dict):
                raise TypeError("session metadata must be a dictionary")
            manifest = {
                "format": "spviz-run",
                "version": 1,
                "name": self.name,
                "created_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._started)
                ),
                "metadata": _validated_json(self.metadata, "session metadata"),
                "products": self.products,
            }
            content = json.dumps(manifest, indent=2, allow_nan=False) + "\n"
            _atomic_write_text(working / "manifest.json", content)
            # Validate the exact staged artifact—not merely its JSON encoding—
            # before it can replace a previously usable run.
            from .server import RunStore

            RunStore(working)
            with _publish_lock(self.path):
                if _run_signature(self.path) != self._initial_signature:
                    raise RuntimeError(
                        "Session path changed while this run was being captured"
                    )
                _replace_directory(working, self.path)
            self._working_path = None
            self._closed = True
            return target


def current_session() -> Session:
    session = _active.get()
    if session is None:
        raise RuntimeError(
            "spviz.capture() must be called inside `with spviz.Session(...)`"
        )
    return session


def capture(
    name: str,
    value: Any,
    *,
    axes: Iterable[str] | None = None,
    view_axes: Iterable[str | int] | None = None,
    coordinates: dict[str | int, Any] | None = None,
    filename: str | Path | None = None,
    views: dict[str, dict[str, Any]] | None = None,
    primary_view: str | None = None,
    representation: Representation = "auto",
    statistics: Statistics = "exact",
    scale: Scale = "linear",
    overview_aspect: Aspect | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    operation: str | None = None,
    upstream: str | Iterable[str] | None = None,
    units: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Capture a value in the active :class:`Session` and return its product ID."""
    return current_session().capture(
        name,
        value,
        axes=axes,
        view_axes=view_axes,
        coordinates=coordinates,
        filename=filename,
        views=views,
        primary_view=primary_view,
        representation=representation,
        statistics=statistics,
        scale=scale,
        overview_aspect=overview_aspect,
        vmin=vmin,
        vmax=vmax,
        operation=operation,
        upstream=upstream,
        units=units,
        metadata=metadata,
    )
