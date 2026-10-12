"""homer TUI, built on Textual: a list and a preview, folders on f. Every window and the reason for it is in
docs/FRAME.md (windows W1-W22, decisions D1-D25); the workflow and limits are in docs/TUI.md. Data lives in store.py."""

from .base import STYLE, readable
from .widgets import FolderPane, Status
from .mainscreen import MainScreen
from .folders import MapScreen, Ticks
from .app import Homer, main

__all__ = ['Homer', 'main', 'MainScreen', 'MapScreen', 'Ticks', 'FolderPane', 'Status', 'STYLE', 'readable']
