from __future__ import annotations

import atexit
import functools
import threading
import weakref
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from .session import Aspect, Representation, Scale, Session, Statistics, WriteMode

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


class Recorder:
    """Passive recorder for values produced by an application-owned pipeline.

    Recorder never invokes processing stages or controls scheduling. ``tap``
    returns its input unchanged after recording an observation.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "Signal-processing run",
        metadata: dict[str, Any] | None = None,
        mode: WriteMode = "replace",
    ):
        self.session = Session(path, name=name, metadata=metadata, mode=mode)
        self._objects: dict[int, tuple[weakref.ReferenceType | Any, str, bool]] = {}
        self._closed = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.session.path

    def __enter__(self) -> Recorder:
        if self._closed:
            raise RuntimeError("Cannot re-enter a closed recorder")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()

    def _remember(self, value: Any, product_id: str) -> None:
        try:
            key = id(value)
            reference = weakref.ref(value, lambda _ref, key=key: self._forget(key))
            weak = True
        except TypeError:
            # Holding a strong reference is preferable to allowing Python object-ID
            # reuse to create false lineage for lists and other array-like values.
            reference = value
            weak = False
        self._objects[id(value)] = (reference, product_id, weak)

    def _forget(self, key: int) -> None:
        with self._lock:
            self._objects.pop(key, None)

    def product_for(self, value: Any) -> str | None:
        with self._lock:
            known = self._objects.get(id(value))
            if known is None:
                return None
            reference, product_id, weak = known
            observed = reference() if weak else reference
            if observed is not value:
                self._objects.pop(id(value), None)
                return None
            return product_id

    def tap(
        self,
        value: T,
        name: str,
        *,
        axes: Iterable[str] | None = None,
        view_axes: Iterable[str | int] | None = None,
        coordinates: dict[str | int, Any] | None = None,
        filename: str | Path | None = None,
        views: dict[str, dict[str, Any]] | None = None,
        representation: Representation = "auto",
        statistics: Statistics = "exact",
        scale: Scale = "linear",
        overview_aspect: Aspect | None = None,
        vmin: float | None = None,
        vmax: float | None = None,
        operation: str | None = None,
        inputs: Any | Iterable[Any] | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """Observe ``value`` and return the exact same object."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot tap values after the recorder is closed")
            known_input = self.product_for(inputs) if inputs is not None else None
            if inputs is None:
                input_values: list[Any] = []
            elif known_input is not None or isinstance(inputs, (str, np.ndarray)):
                input_values = [inputs]
            elif isinstance(inputs, Iterable):
                input_values = list(inputs)
            else:
                input_values = [inputs]
            known_ids = {product["id"] for product in self.session.products}
            upstream: list[str] = []
            for item in input_values:
                product_id = item if isinstance(item, str) else self.product_for(item)
                if product_id is None:
                    continue
                if product_id not in known_ids:
                    raise ValueError(f"Unknown upstream product: {product_id}")
                if product_id not in upstream:
                    upstream.append(product_id)
            product_id = self.session.capture(
                name,
                value,
                axes=axes,
                view_axes=view_axes,
                coordinates=coordinates,
                filename=filename,
                views=views,
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
            self._remember(value, product_id)
        return value

    def instrument(
        self,
        function: F | None = None,
        *,
        name: str | None = None,
        axes: Iterable[str] | None = None,
        view_axes: Iterable[str | int] | None = None,
        coordinates: dict[str | int, Any] | None = None,
        filename: str | Path | None = None,
        views: dict[str, dict[str, Any]] | None = None,
        representation: Representation = "auto",
        statistics: Statistics = "exact",
        scale: Scale = "linear",
        overview_aspect: Aspect | None = None,
        vmin: float | None = None,
        vmax: float | None = None,
        operation: str | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> F | Callable[[F], F]:
        """Observe a function's array result without changing who calls it."""

        def decorate(target: F) -> F:
            @functools.wraps(target)
            def wrapped(*args, **kwargs):
                result = target(*args, **kwargs)
                observed_inputs = [
                    item for item in (*args, *kwargs.values()) if self.product_for(item)
                ]
                self.tap(
                    result,
                    name or target.__name__,
                    axes=axes,
                    view_axes=view_axes,
                    coordinates=coordinates,
                    filename=filename,
                    views=views,
                    representation=representation,
                    statistics=statistics,
                    scale=scale,
                    overview_aspect=overview_aspect,
                    vmin=vmin,
                    vmax=vmax,
                    operation=operation or target.__name__,
                    inputs=observed_inputs,
                    units=units,
                    metadata=metadata,
                )
                return result

            return wrapped  # type: ignore[return-value]

        return decorate(function) if function is not None else decorate

    def close(self) -> Path:
        with self._lock:
            if not self._closed:
                manifest = self.session.close()
                self._closed = True
                self._objects.clear()
                return manifest
            return self.path / "manifest.json"

    def abort(self) -> None:
        """Discard an unfinished recording without replacing a previous run."""
        with self._lock:
            if not self._closed:
                self.session.abort()
                self._closed = True
                self._objects.clear()


_default: Recorder | None = None


def init(
    path: str | Path,
    *,
    name: str = "Signal-processing run",
    metadata: dict[str, Any] | None = None,
    mode: WriteMode = "replace",
) -> Recorder:
    """Configure process-wide passive observation for an existing application."""
    global _default
    if _default is not None:
        _default.close()
    _default = Recorder(path, name=name, metadata=metadata, mode=mode)
    return _default


def get_recorder() -> Recorder:
    if _default is None:
        raise RuntimeError("Call spviz.init(...) before using spviz.tap()")
    return _default


def tap(
    value: T,
    name: str,
    *,
    axes: Iterable[str] | None = None,
    view_axes: Iterable[str | int] | None = None,
    coordinates: dict[str | int, Any] | None = None,
    filename: str | Path | None = None,
    views: dict[str, dict[str, Any]] | None = None,
    representation: Representation = "auto",
    statistics: Statistics = "exact",
    scale: Scale = "linear",
    overview_aspect: Aspect | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    operation: str | None = None,
    inputs: Any | Iterable[Any] | None = None,
    units: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> T:
    """Record ``value`` with the default recorder and return the same object."""
    return get_recorder().tap(
        value,
        name,
        axes=axes,
        view_axes=view_axes,
        coordinates=coordinates,
        filename=filename,
        views=views,
        representation=representation,
        statistics=statistics,
        scale=scale,
        overview_aspect=overview_aspect,
        vmin=vmin,
        vmax=vmax,
        operation=operation,
        inputs=inputs,
        units=units,
        metadata=metadata,
    )


def instrument(function: F | None = None, **capture_options: Any):
    """Instrument a function using whichever global recorder is active at call time."""

    def decorate(target: F) -> F:
        @functools.wraps(target)
        def wrapped(*args, **kwargs):
            observed = get_recorder().instrument(target, **capture_options)
            return observed(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorate(function) if function is not None else decorate


def close() -> Path:
    return get_recorder().close()


def _abort_default() -> None:
    if _default is not None:
        _default.abort()


atexit.register(_abort_default)
