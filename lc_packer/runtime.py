"""Persistent paths and external-tool setup for a portable Windows bundle."""
import os
from pathlib import Path
import sys


def application_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def external_tool_path(value, bundle_root):
    root = Path(bundle_root).resolve()
    return os.pathsep.join(entry for entry in value.split(os.pathsep)
                           if entry and not Path(entry).resolve().is_relative_to(root))


def prepare_external_tools():
    """Called after Qt is loaded; selected tools must use their own DLLs."""
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes
        if not ctypes.windll.kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError()
        os.environ["PATH"] = external_tool_path(os.environ.get("PATH", ""), sys._MEIPASS)
