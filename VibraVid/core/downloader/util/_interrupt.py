# 22.02.25

import signal
import time

from rich.console import Console

console = Console()


class InterruptHandler:
    def __init__(self, window_seconds: float = 2.0, force_presses: int = 3) -> None:
        self.interrupt_count: int = 0
        self.last_interrupt_time: float = 0.0
        self.kill_download: bool = False
        self.force_quit: bool = False
        self.window_seconds: float = float(window_seconds)
        self.force_presses: int = int(force_presses)

    def handle(self, signum, frame, original_handler) -> None:
        """Signal callback — attach with ``partial(self.handle, original_handler=prev)``."""
        now = time.time()
        if now - self.last_interrupt_time > self.window_seconds:
            self.interrupt_count = 0

        self.interrupt_count += 1
        self.last_interrupt_time = now

        if self.interrupt_count == 1:
            self.kill_download = True
            console.print("\n[yellow]First interrupt received. Download will complete and save. Press Ctrl+C three times quickly to force quit.")

        elif self.interrupt_count >= self.force_presses:
            self.force_quit = True
            console.print("\n[red]Force quit activated. Saving partial download...")
            signal.signal(signum, original_handler)
