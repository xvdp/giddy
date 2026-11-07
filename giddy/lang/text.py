"""Fallback adapter: the whole file is one unit, reported as a line diff without full-code blocks."""
from __future__ import annotations

from .base import Adapter, Line, Unit, split_lines


class TextAdapter(Adapter):
    name = "text"
    full_code = False

    def __init__(self, fence: str = ""):
        self.fence = fence

    def units(self, source: str, code_only: bool) -> list[Unit]:
        lines = [Line(i, t.rstrip()) for i, t in enumerate(split_lines(source), 1)]
        if code_only:
            signature = "\n".join(l.text for l in lines if l.text.strip())
        else:
            signature = source
        return [Unit("<file>", "text", "file", lines, signature)]
