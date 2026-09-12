"""Passively observe and visualize signal-processing data products."""

from .observer import Recorder, close, get_recorder, init, instrument, tap
from .session import (
    Aspect,
    Representation,
    Scale,
    Session,
    Statistics,
    WriteMode,
    capture,
)

__all__ = [
    "Aspect",
    "Recorder",
    "Representation",
    "Scale",
    "Session",
    "Statistics",
    "WriteMode",
    "capture",
    "close",
    "get_recorder",
    "init",
    "instrument",
    "tap",
]
__version__ = "0.2.0"
