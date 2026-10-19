"""Proven LC artifact inputs for packaging.

Source compilation is unavailable until a stable callable compiler is found.
Manual capture only observes a caller-selected compile.lc file; the caller is
responsible for associating that output with the intended source project.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
from threading import Event, Lock
import time
import uuid


MAX_ARTIFACT_BYTES = 128 * 1024 * 1024


class CompilerUnavailable(RuntimeError):
    """A compiler output cannot be safely obtained through this adapter."""


class CaptureTimeout(CompilerUnavailable):
    """No fresh, stable LC output appeared before the deadline."""


class CaptureCancelled(CompilerUnavailable):
    """The caller cancelled a manual capture session."""


@dataclass(frozen=True, slots=True)
class CompiledScript:
    path: Path
    sha256: str
    mode: str


def compile_source(source_path: Path) -> CompiledScript:
    """Reject automatic compilation until a callable compiler is validated."""
    raise CompilerUnavailable(
        f"Cannot compile {Path(source_path)}: no stable callable compiler CLI/API "
        "has been validated for the installed assistant. Compile manually in "
        "the 3.7.2 assistant and use ManualCaptureSession or load_precompiled."
    )


def _check_limit(max_bytes: int) -> None:
    if max_bytes < 1:
        raise CompilerUnavailable("Artifact size limit must be positive")


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino


def _read_candidate(path: Path, max_bytes: int) -> tuple[bytes, tuple[int, int, int, int]] | None:
    """Read a regular file within a bound, or return None during a file change."""
    try:
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CompilerUnavailable(f"Artifact is not a regular file: {path}")
            if before.st_size > max_bytes:
                raise CompilerUnavailable(f"Artifact exceeds {max_bytes} bytes: {path}")
            data = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())
        current = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CompilerUnavailable(f"Cannot read artifact: {path}: {exc}") from exc
    if len(data) > max_bytes:
        raise CompilerUnavailable(f"Artifact exceeds {max_bytes} bytes: {path}")
    identity = _file_identity(after)
    if len(data) != before.st_size or _file_identity(before) != identity or identity != _file_identity(current):
        return None
    return data, identity


def load_precompiled(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> CompiledScript:
    """Fingerprint one explicit nonempty file, without claiming source compilation."""
    _check_limit(max_bytes)
    path = Path(path)
    observed = _read_candidate(path, max_bytes)
    if observed is None:
        raise CompilerUnavailable(f"Precompiled artifact is missing or changing: {path}")
    data, _ = observed
    if not data:
        raise CompilerUnavailable(f"Precompiled artifact is empty: {path}")
    return CompiledScript(path, hashlib.sha256(data).hexdigest(), "precompiled")


class ManualCaptureSession:
    """Capture a fresh compile.lc created by a caller-managed manual debug run.

    Construct this before starting debug. ``wait`` polls only the selected path,
    accepts changed bytes after this baseline, and copies an independent snapshot
    into the existing caller-owned output directory. The source association is
    user-managed; this adapter does not operate the assistant or its compiler.
    """

    def __init__(
        self, lc_path: Path, output_dir: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES
    ) -> None:
        _check_limit(max_bytes)
        self.lc_path = Path(lc_path)
        self.output_dir = Path(output_dir)
        if self.lc_path.name.casefold() != "compile.lc":
            raise CompilerUnavailable("Manual capture requires an explicitly selected compile.lc path")
        if not self.output_dir.is_dir():
            raise CompilerUnavailable(f"Capture output directory must already exist: {self.output_dir}")
        baseline = _read_candidate(self.lc_path, max_bytes)
        if baseline is None and self.lc_path.exists():
            raise CompilerUnavailable(f"Cannot establish a stable baseline for {self.lc_path}")
        self._baseline_sha256 = hashlib.sha256(baseline[0]).hexdigest() if baseline else None
        self._max_bytes = max_bytes
        self._wait_lock = Lock()
        self._used = False

    def wait(
        self,
        *,
        timeout_seconds: float,
        cancel_event: Event | None = None,
        poll_interval: float = 0.1,
    ) -> CompiledScript:
        """Wait for fresh stable bytes, or raise timeout/cancellation explicitly."""
        if not math.isfinite(timeout_seconds) or not math.isfinite(poll_interval) or timeout_seconds < 0 or poll_interval <= 0:
            raise CompilerUnavailable("Capture timeout and poll interval must be finite; timeout nonnegative and interval positive")
        with self._wait_lock:
            if self._used:
                raise CompilerUnavailable("Capture sessions are one-shot; start a new session before compiling again")
            self._used = True
        deadline = time.monotonic() + timeout_seconds
        previous: tuple[bytes, tuple[int, int, int, int]] | None = None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CaptureCancelled(f"Manual capture cancelled: {self.lc_path}")
            observed = _read_candidate(self.lc_path, self._max_bytes)
            if observed is not None:
                data, identity = observed
                fresh = bool(data) and hashlib.sha256(data).hexdigest() != self._baseline_sha256
                if fresh:
                    if previous == observed:
                        return self._snapshot(data)
                    previous = (data, identity)
                else:
                    previous = None
            else:
                previous = None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptureTimeout(f"No fresh stable compile.lc appeared within {timeout_seconds}s: {self.lc_path}")
            delay = min(poll_interval, remaining)
            if cancel_event is None:
                time.sleep(delay)
            elif cancel_event.wait(delay):
                raise CaptureCancelled(f"Manual capture cancelled: {self.lc_path}")

    def _snapshot(self, data: bytes) -> CompiledScript:
        digest = hashlib.sha256(data).hexdigest()
        for _ in range(3):
            path = self.output_dir / f"capture-{digest[:12]}-{uuid.uuid4().hex}.lc"
            try:
                with path.open("xb") as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                path.chmod(stat.S_IREAD)
            except FileExistsError:
                continue
            except OSError as exc:
                path.unlink(missing_ok=True)
                raise CompilerUnavailable(f"Cannot save captured LC snapshot: {path}: {exc}") from exc
            return CompiledScript(path, digest, "manual-capture")
        raise CompilerUnavailable(f"Cannot allocate a unique LC snapshot in {self.output_dir}")
