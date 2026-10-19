"""Behavior checks for explicit and manually captured compiler artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest

from lc_packer.compiler import (
    CaptureCancelled,
    CaptureTimeout,
    CompilerUnavailable,
    ManualCaptureSession,
    compile_source,
    load_precompiled,
)


class CompilerArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lc = self.root / "compile.lc"
        self.output = self.root / "snapshots"
        self.output.mkdir()

    def test_explicit_precompiled_file_is_fingerprinted_and_labeled(self) -> None:
        self.lc.write_bytes(b"compiled bytes")

        artifact = load_precompiled(self.lc)

        self.assertEqual(artifact.path, self.lc)
        self.assertEqual(artifact.sha256, "c1808bc6559670d5e436d48975dc8f1991da1e72fc0c5a5b74178d5451fae55a")
        self.assertEqual(artifact.mode, "precompiled")

    def test_precompiled_missing_and_empty_files_fail(self) -> None:
        with self.assertRaises(CompilerUnavailable):
            load_precompiled(self.lc)
        self.lc.touch()
        with self.assertRaises(CompilerUnavailable):
            load_precompiled(self.lc)

    def test_precompiled_read_is_bounded(self) -> None:
        self.lc.write_bytes(b"six!!!")
        with self.assertRaises(CompilerUnavailable):
            load_precompiled(self.lc, max_bytes=5)

    def test_source_compilation_explains_missing_capability(self) -> None:
        with self.assertRaisesRegex(CompilerUnavailable, "stable callable compiler CLI/API"):
            compile_source(self.root / "project.mqb")

    def test_stale_existing_file_times_out_without_snapshot(self) -> None:
        self.lc.write_bytes(b"stale")
        capture = ManualCaptureSession(self.lc, self.output)

        with self.assertRaises(CaptureTimeout):
            capture.wait(timeout_seconds=0.06, poll_interval=0.01)

        self.assertEqual(list(self.output.iterdir()), [])

    def test_capture_session_cannot_report_same_payload_twice(self) -> None:
        capture = ManualCaptureSession(self.lc, self.output)
        self.lc.write_bytes(b"fresh")
        capture.wait(timeout_seconds=1, poll_interval=0.01)
        with self.assertRaises(CompilerUnavailable):
            capture.wait(timeout_seconds=0.05, poll_interval=0.01)
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_nonfinite_capture_parameters_fail_without_polling(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        for timeout, interval in ((float("nan"), 0.1), (float("inf"), 0.1), (1, float("nan")), (1, float("inf"))):
            capture = ManualCaptureSession(self.lc, self.output)
            with self.assertRaisesRegex(CompilerUnavailable, "finite"):
                capture.wait(timeout_seconds=timeout, poll_interval=interval, cancel_event=cancelled)

    def test_new_file_after_start_is_snapshotted_with_manual_label(self) -> None:
        capture = ManualCaptureSession(self.lc, self.output)
        writer = threading.Thread(target=self._write_after_delay, args=(b"new LC",))
        writer.start()
        self.addCleanup(writer.join)

        artifact = capture.wait(timeout_seconds=1, poll_interval=0.01)

        self.assertEqual(artifact.mode, "manual-capture")
        self.assertEqual(artifact.sha256, hashlib.sha256(b"new LC").hexdigest())
        self.assertEqual(artifact.path.parent, self.output)
        self.assertEqual(artifact.path.read_bytes(), b"new LC")
        self.assertFalse(artifact.path.stat().st_mode & stat.S_IWRITE)
        self.lc.write_bytes(b"later change")
        self.assertEqual(artifact.path.read_bytes(), b"new LC")

    def test_changed_existing_bytes_are_captured_without_overwriting_prior_snapshot(self) -> None:
        self.lc.write_bytes(b"old")
        prior = self.output / "prior.lc"
        prior.write_bytes(b"keep")
        capture = ManualCaptureSession(self.lc, self.output)
        writer = threading.Thread(target=self._write_after_delay, args=(b"changed",))
        writer.start()
        self.addCleanup(writer.join)

        artifact = capture.wait(timeout_seconds=1, poll_interval=0.01)

        self.assertEqual(artifact.path.read_bytes(), b"changed")
        self.assertEqual(prior.read_bytes(), b"keep")

    def test_rewritten_identical_bytes_do_not_count_as_fresh(self) -> None:
        self.lc.write_bytes(b"same")
        capture = ManualCaptureSession(self.lc, self.output)
        writer = threading.Thread(target=self._write_after_delay, args=(b"same",))
        writer.start()
        self.addCleanup(writer.join)

        with self.assertRaises(CaptureTimeout):
            capture.wait(timeout_seconds=0.09, poll_interval=0.01)

        self.assertEqual(list(self.output.iterdir()), [])

    def test_empty_new_file_is_not_captured(self) -> None:
        capture = ManualCaptureSession(self.lc, self.output)
        self.lc.touch()
        with self.assertRaises(CaptureTimeout):
            capture.wait(timeout_seconds=0.05, poll_interval=0.01)

    def test_cancellation_is_explicit_and_creates_no_snapshot(self) -> None:
        capture = ManualCaptureSession(self.lc, self.output)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CaptureCancelled):
            capture.wait(timeout_seconds=1, cancel_event=cancelled)
        self.assertEqual(list(self.output.iterdir()), [])

    def _write_after_delay(self, content: bytes) -> None:
        time.sleep(0.025)
        self.lc.write_bytes(content)


if __name__ == "__main__":
    unittest.main()
