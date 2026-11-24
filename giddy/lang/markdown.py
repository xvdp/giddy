"""Markdown adapter: the fenced code blocks of a markdown file in the languages giddy parses; prose, and fences
in other languages (bash, json, text, unlabelled), are not compared.

Fence      CommonMark: an opening run of 3+ backticks or tildes (any indentation), info string, content lines
           with up to the opening indentation removed, closing run of the same character at least as long;
           an unclosed fence runs to the end of the file.
Language   first word of the info string (`{.python}` accepted), through `ALIASES` to an extension, then the
           extension registry: ```python, ```py, ```ts, ```cpp, ```rust, ```julia, ...
Units      per language, the fences that parse alone are laid on a blank copy of the file (markdown line
           numbers kept) and parsed as one source by the language adapter: functions and classes pair by
           qualified name, as in a source file; module-level statements of every fence form one unit, label
           `<lang> fences`. Keys get a `<lang>:` prefix.
Fragments  a fence that does not parse alone (`...` elisions, `>>>` prompts, notebook magics): one unit per
           fence, key `<lang>:fence#<n>` (n-th fragment of that language), compared line by line; code-only
           mode drops its blank lines only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .base import Adapter, Line, Unit, split_lines

# info string word -> registry extension (words that are themselves registered extensions need no entry)
ALIASES = {"python": "py", "python3": "py", "py3": "py", "typescript": "ts", "javascript": "js", "node": "js",
           "c++": "cpp", "cuda": "cu", "rust": "rs", "julia": "jl"}

_OPEN = re.compile(r"^(?P<indent> *)(?P<mark>`{3,}|~{3,})(?P<info>.*)$")


@dataclass
class Fence:
    lang: str                           # info string word through ALIASES; "" when absent
    body: list[tuple[int, str]]         # (1-based markdown line number, content line)
    close: int                          # line number of the closing run; one past the end when unclosed

    @property
    def source(self) -> str:
        return "".join(t + "\n" for _, t in self.body)


def fences(lines: list[str]) -> list[Fence]:
    out: list[Fence] = []
    i = 0
    while i < len(lines):
        m = _OPEN.match(lines[i])
        if not m or (m["mark"][0] == "`" and "`" in m["info"]):
            i += 1
            continue
        indent, mark = len(m["indent"]), m["mark"]
        close = re.compile(rf"^ *{re.escape(mark[0])}{{{len(mark)},}}[ \t]*$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j]):
            j += 1
        word = (m["info"].split() or [""])[0].strip("{}").lstrip(".").lower()
        body = [(n + 1, _dedent(lines[n], indent)) for n in range(i + 1, j)]
        out.append(Fence(ALIASES.get(word, word), body, j + 1))
        i = j + 1
    return out


def _dedent(line: str, indent: int) -> str:
    n = len(line) - len(line.lstrip(" "))
    return line[min(n, indent):]


class MarkdownAdapter(Adapter):
    name = "markdown"
    fence = ""                          # added-file display: fences of several languages, no single highlight

    def __init__(self, registry: dict[str, Adapter]):
        self.registry = registry

    def _groups(self, source: str) -> tuple[int, dict[str, tuple[Adapter, list[Fence], list[Fence]]]]:
        """Line count; per language fence name: adapter, fences that parse alone, fragments."""
        lines = split_lines(source)
        groups: dict[str, tuple[Adapter, list[Fence], list[Fence]]] = {}
        for f in fences(lines):
            a = self.registry.get(f.lang)
            if a is None:
                continue
            _, whole, frags = groups.setdefault(a.fence, (a, [], []))
            (whole if a.units(f.source, True) is not None else frags).append(f)
        return len(lines), groups

    @staticmethod
    def _blank_copy(n: int, whole: list[Fence]) -> str:
        """Source of `n` lines: fence content at its markdown line numbers, every other line empty."""
        text = [""] * n
        for f in whole:
            for no, t in f.body:
                text[no - 1] = t
        return "".join(t + "\n" for t in text)

    def units(self, source: str, code_only: bool) -> list[Unit] | None:
        n, groups = self._groups(source)
        out: list[Unit] = []
        for lang, (a, whole, frags) in groups.items():
            units = a.units(self._blank_copy(n, whole), code_only) if whole else []
            if units is None:           # fences parse alone but not together: compare each as a fragment
                units, frags = [], sorted(whole + frags, key=lambda f: f.close)
            for u in units:
                label = f"{lang} fences" if u.kind == "module" else u.label
                out.append(replace(u, key=f"{lang}:{u.key}", label=label, fence=lang))
            for i, f in enumerate(frags, 1):
                lines = [Line(no, t.rstrip()) for no, t in f.body]
                if code_only:
                    lines = [l for l in lines if l.text.strip()]
                if lines:
                    out.append(Unit(f"{lang}:fence#{i}", "text", f"{lang} fence L{lines[0].no}", lines,
                                    "\n".join(l.text for l in lines), fence=lang))
        return sorted(out, key=lambda u: (u.start, u.key))

    def code(self, source: str, code_only: bool) -> list[Line]:
        """Code of the parsed fences, normalized as in `units`, one blank line after each fence."""
        n, groups = self._groups(source)
        out: list[tuple[int, list[Line]]] = []
        for a, whole, frags in groups.values():
            norm = {l.no: l.text for l in a.code(self._blank_copy(n, whole), code_only)} if whole else {}
            for f in whole:
                out.append((f.close, [Line(no, norm[no]) for no, _ in f.body if no in norm]))
            for f in frags:
                out.append((f.close, [Line(no, t.rstrip()) for no, t in f.body if t.strip() or not code_only]))
        return [l for close, ls in sorted(out, key=lambda x: x[0]) for l in ls + [Line(close, "")]]
