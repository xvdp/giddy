"""Language adapter interface.

An adapter splits one source file into units. A unit is the granularity at which a change is
reported and displayed in full: a function, a class header with its non-function body statements,
or the module-level statements. Units of the old and new file pair by `key`; two units with equal
`signature` count as unchanged.

Adapters for brace languages (rust, ts, glsl, slang) are expected to implement the same interface,
e.g. on tree-sitter grammars.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Line:
    """One displayed source line: original 1-based line number and normalized text."""
    no: int
    text: str


@dataclass
class Unit:
    key: str                    # pairing key, unique per file: "Klass.method", "<module>", "name#2"
    kind: str                   # "function" | "class" | "module" | "text"
    label: str                  # display name
    lines: list[Line]           # normalized lines in file order
    signature: str              # equality => unchanged
    blocks: list[tuple[int, int]] | None = field(default=None)
    # blocks: inclusive line spans of the statements composing a class/module unit. A report shows
    # only the blocks that contain a change. None => show every line (functions) or no full code (text).

    @property
    def start(self) -> int:
        return self.lines[0].no if self.lines else 0

    @property
    def end(self) -> int:
        return self.lines[-1].no if self.lines else 0


class Adapter:
    name = "base"
    fence = ""
    full_code = True            # units are scopes: a modified function is diffed whole

    def units(self, source: str, code_only: bool) -> list[Unit] | None:
        """Units of `source` ordered by start line; None when the source cannot be parsed."""
        raise NotImplementedError

    def code(self, source: str, code_only: bool) -> list[Line]:
        """Every displayed line of `source`, normalized as in `units`."""
        return [Line(i, t.rstrip()) for i, t in enumerate(split_lines(source), 1)]


def split_lines(source: str) -> list[str]:
    """Split on \\n, \\r\\n, \\r only, matching the line numbering of the Python tokenizer and git.

    str.splitlines also splits on \\f, \\v, \\x1c..\\x1e, \\x85, \\u2028, \\u2029 and would shift line numbers.
    """
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines
