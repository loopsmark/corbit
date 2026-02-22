"""Prevent macOS from sleeping while corbit is running."""

from __future__ import annotations

import platform
import shutil
import subprocess
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def prevent_sleep() -> Iterator[None]:
    """Keep the system awake on macOS using caffeinate.

    On non-macOS platforms or if caffeinate is not available, this is a no-op.
    """
    if platform.system() != "Darwin" or shutil.which("caffeinate") is None:
        yield
        return

    proc = subprocess.Popen(
        ["caffeinate", "-s"],  # -s: prevent system sleep
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        yield
    finally:
        proc.terminate()
        proc.wait()
