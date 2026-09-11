"""Passively observe and visualize signal-processing data products."""

from .observer import Recorder, close, get_recorder, init, instrument, tap
from .session import Session

__all__ = ["Recorder", "Session", "close", "get_recorder", "init", "instrument", "tap"]
__version__ = "0.1.0"
