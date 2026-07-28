"""Repetition detector for thinking streams."""

from __future__ import annotations


class ThinkLoopDetector:
    def __init__(self, *, block_sizes: list[int] | None = None, block_repeat: int = 3):
        self.block_sizes = block_sizes or [1, 2, 3]
        self.block_repeat = block_repeat
        self._lines: list[str] = []
        self._block_hits: dict[int, int] = {bl: 0 for bl in self.block_sizes}

    def feed_line(self, line: str) -> str | None:
        """Return an error string if a loop is detected, else None."""
        line = line.rstrip("\n")
        if not line.strip():
            return None
        self._lines.append(line)
        n = len(self._lines)
        for bl in self.block_sizes:
            if n < bl * 2:
                continue
            a = self._lines[-bl:]
            b = self._lines[-2 * bl : -bl]
            if a == b:
                self._block_hits[bl] = self._block_hits.get(bl, 0) + 1
                if self._block_hits[bl] >= self.block_repeat:
                    return f"block_cycle {bl}x{self._block_hits[bl]}"
            else:
                self._block_hits[bl] = 0
        return None
