"""A tiny spinner for the long, silent waits.

The model call takes 15-60s at high effort and produces no output while it
runs, so a user reasonably assumes the program has hung. This shows a live
"working" line with an elapsed-seconds counter during those waits, then clears
it so the real log line lands cleanly.

Kept deliberately simple and safe:
- ASCII frames only, so it can't trip the console's encoding.
- Animates only on a real terminal; when output is redirected to a file it
  prints one static line instead of spraying carriage returns into the log.
- The worker is a daemon thread, so it can never hold the program open.
"""

from __future__ import annotations

import itertools
import sys
import threading
import time


class Spinner:
    FRAMES = "|/-\\"

    def __init__(self, message: str, stream=None, interval: float = 0.15):
        self.message = message
        self.stream = stream if stream is not None else sys.stdout
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = 0.0
        try:
            self.animate = self.stream.isatty()
        except (AttributeError, ValueError):
            self.animate = False

    def __enter__(self) -> "Spinner":
        self._start = time.monotonic()
        if self.animate:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            # Redirected to a file: one static line so the wait is still visible.
            self._write(self.message + "\n")
        return self

    def _run(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            elapsed = int(time.monotonic() - self._start)
            self._write(f"\r{self.message} {frame} {elapsed}s ")
            self._stop.wait(self.interval)

    def __exit__(self, *exc) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            # Wipe the spinner line so the next log() prints from a clean start.
            self._write("\r" + " " * (len(self.message) + 12) + "\r")

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (ValueError, OSError):
            pass  # Stream closed mid-write; nothing worth crashing over.
