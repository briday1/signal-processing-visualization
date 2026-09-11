from __future__ import annotations

import atexit
import functools
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

import numpy as np

from .session import Session

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


class Recorder:
    """Passive recorder for values produced by an application-owned pipeline.

    Recorder never invokes processing stages or controls scheduling. ``tap``
    returns its input unchanged after recording an observation.
    """

    def __init__(self, path: str | Path, *, name: str = "Signal-processing run", metadata: dict[str, Any] | None = None):
        self.session = Session(path, name=name, metadata=metadata)
        self.session.path.mkdir(parents=True, exist_ok=True)
        (self.session.path / "arrays").mkdir(exist_ok=True)
        self._objects: dict[int, tuple[weakref.ReferenceType | None, str]] = {}
        self._closed = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.session.path

    def _remember(self, value: Any, product_id: str) -> None:
        try:
            reference = weakref.ref(value, lambda _ref, key=id(value): self._objects.pop(key, None))
        except TypeError:
            reference = None
        self._objects[id(value)] = (reference, product_id)

    def product_for(self, value: Any) -> str | None:
        known = self._objects.get(id(value))
        if known is None:
            return None
        reference, product_id = known
        if reference is not None and reference() is not value:
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
        scale: str = "linear",
        operation: str | None = None,
        inputs: Any | Iterable[Any] | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        """Observe ``value`` and return the exact same object."""
        if self._closed:
            raise RuntimeError("Cannot tap values after the recorder is closed")
        if inputs is None:
            input_values: list[Any] = []
        elif isinstance(inputs, (list, tuple)):
            input_values = list(inputs)
        else:
            input_values = [inputs]
        upstream = [product_id for item in input_values if (product_id := self.product_for(item))]
        with self._lock:
            product_id = self.session.capture(
                name,
                value,
                axes=axes,
                view_axes=view_axes,
                coordinates=coordinates,
                filename=filename,
                scale=scale,
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
        scale: str = "linear",
        operation: str | None = None,
        units: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> F | Callable[[F], F]:
        """Observe a function's array result without changing who calls it."""
        def decorate(target: F) -> F:
            @functools.wraps(target)
            def wrapped(*args, **kwargs):
                result = target(*args, **kwargs)
                observed_inputs = [item for item in (*args, *kwargs.values()) if self.product_for(item)]
                self.tap(
                    result,
                    name or target.__name__,
                    axes=axes,
                    view_axes=view_axes,
                    coordinates=coordinates,
                    filename=filename,
                    scale=scale,
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
                return manifest
            return self.path / "manifest.json"


_default: Recorder | None = None


def init(path: str | Path, *, name: str = "Signal-processing run", metadata: dict[str, Any] | None = None) -> Recorder:
    """Configure process-wide passive observation for an existing application."""
    global _default
    if _default is not None:
        _default.close()
    _default = Recorder(path, name=name, metadata=metadata)
    return _default


def get_recorder() -> Recorder:
    if _default is None:
        raise RuntimeError("Call spviz.init(...) before using spviz.tap()")
    return _default


def tap(value: T, name: str, **kwargs: Any) -> T:
    return get_recorder().tap(value, name, **kwargs)


def instrument(function: F | None = None, **kwargs: Any):
    return get_recorder().instrument(function, **kwargs)


def close() -> Path:
    return get_recorder().close()


def _close_default() -> None:
    if _default is not None:
        _default.close()


atexit.register(_close_default)
