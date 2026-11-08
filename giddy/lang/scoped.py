"""Unit assembly shared by the token-based adapters (C family, Rust, JS/TS, GLSL, Slang, Julia).

A structural parser yields `Item`s: token index ranges over the code tokens (comments excluded).
`build` turns items into `Unit`s:

    lines       code mode: comments and dropped tokens (Julia docstrings) cut from each line; a line
                left blank by the cut is removed. Raw mode: every line, trailing whitespace stripped.
    signature   code mode: token texts of the item joined, inactive preprocessor branches included; formatting, comments and docstrings never
                reach it, so a unit changed only in those compares equal. Raw mode: source text.
    key         qualified name. A name defined more than once (overloads, multiple dispatch) gets its
                parameter text appended, `f(int a)`; identical keys then get `#2`, `#3` in source order.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field

from .base import Line, Unit, split_lines
from .lexer import LexSpec, Tok, tokenize


class ParseError(ValueError):
    """Unbalanced brackets or blocks; the adapter returns None and the file falls back to a line diff."""


@dataclass
class Item:
    kind: str                           # function | class | module
    name: str                           # qualified name
    label: str
    spans: list[tuple[int, int]]        # inclusive code-token index ranges
    params: str = ""                    # parameter text, used only for repeated names
    blocks: bool = False                # class/module: display only touched statements
    lead: list[int] = field(default_factory=list)   # raw mode: extra line starts (Julia docstrings)


class Source:
    def __init__(self, text: str, spec: LexSpec):
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.raw = split_lines(self.text)
        self.toks = tokenize(self.text, spec)
        inactive = self.inactive() if spec.preprocessor else set()
        self.code = [t for k, t in enumerate(self.toks) if t.kind != "com" and k not in inactive]
        self.offsets = [t.start for t in self.toks]
        self.starts = [0]
        for line in self.raw:
            self.starts.append(self.starts[-1] + len(line) + 1)

    def inactive(self) -> set[int]:
        """Token positions inside preprocessor branches other than the first possible one.

        `#if 0` branches count as impossible. Structure follows one branch, so `#if A ... {` /
        `#else ... {` / `#endif` pairs do not unbalance braces. Signatures still use every token.
        """
        out: set[int] = set()
        stack: list[list[bool]] = []                    # [current branch active, a branch taken]
        for k, t in enumerate(self.toks):
            if t.kind == "pre":
                words = t.text[1:].replace("(", " ").split()
                d = words[0] if words else ""
                if d in ("if", "ifdef", "ifndef"):
                    cur = not (d == "if" and words[1:2] == ["0"])
                    stack.append([cur, cur])
                elif d in ("elif", "elifdef", "elifndef", "else") and stack:
                    cur = not stack[-1][1]
                    stack[-1] = [cur, stack[-1][1] or cur]
                elif d == "endif" and stack:
                    stack.pop()
                continue
            if any(not cur for cur, _ in stack):
                out.add(k)
        return out

    def tokens(self, a: Tok, b: Tok) -> list[Tok]:
        """Every token from `a` to `b` inclusive, inactive branches and comments included."""
        lo = bisect_left(self.offsets, a.start)
        hi = bisect_right(self.offsets, b.start)
        return self.toks[lo:hi]

    def match(self, opens: str = "([{", closes: str = ")]}") -> dict[int, int]:
        """Index of the matching closer for every opener in `code` (both directions)."""
        pairs = dict(zip(closes, opens))
        stack: list[int] = []
        out: dict[int, int] = {}
        for i, t in enumerate(self.code):
            if t.kind != "op":
                continue
            if t.text in opens:
                stack.append(i)
            elif t.text in pairs:
                if not stack or self.code[stack[-1]].text != pairs[t.text]:
                    raise ParseError(f"unbalanced {t.text!r} at line {t.line}")
                j = stack.pop()
                out[j], out[i] = i, j
        if stack:
            raise ParseError(f"unclosed {self.code[stack[-1]].text!r} at line {self.code[stack[-1]].line}")
        return out

    def normalized(self, code_only: bool, drop: list[Tok]) -> dict[int, str]:
        if not code_only:
            return {i: t.rstrip() for i, t in enumerate(self.raw, 1)}
        cuts: dict[int, list[tuple[int, int]]] = {}
        for t in [t for t in self.toks if t.kind == "com"] + drop:
            for ln in range(t.line, t.eline + 1):
                base = self.starts[ln - 1]
                a = t.start - base if ln == t.line else 0
                b = t.end - base if ln == t.eline else len(self.raw[ln - 1])
                cuts.setdefault(ln, []).append((a, b))
        out = {}
        for i, line in enumerate(self.raw, 1):
            if i in cuts:
                kept, pos = [], 0
                for a, b in sorted(cuts[i]):
                    kept.append(line[pos:a])
                    pos = max(pos, b)
                kept.append(line[pos:])
                cut = "".join(kept)
                if line.strip() and not cut.strip():
                    continue
                line = cut
            out[i] = line.rstrip()
        return out


def _keys(items: list[Item]) -> list[str]:
    counts = Counter(it.name for it in items)
    base = [f"{it.name}({it.params})" if counts[it.name] > 1 and it.params else it.name for it in items]
    seen: Counter = Counter()
    keys = []
    for k in base:
        seen[k] += 1
        keys.append(k if seen[k] == 1 else f"{k}#{seen[k]}")
    return keys


def build(src: Source, items: list[Item], code_only: bool, drop: list[Tok] | None = None) -> list[Unit]:
    drop = drop or []
    text = src.normalized(code_only, drop)
    dropped = {id(t) for t in drop}
    code = src.code
    units = []
    for it, key in zip(items, _keys(items)):
        spans = sorted((code[a].line, code[b].eline) for a, b in it.spans if a <= b)
        if not code_only and it.lead and spans:
            spans[0] = (min(it.lead + [spans[0][0]]), spans[0][1])
        merged: list[tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        nos = sorted({i for a, b in merged for i in range(a, b + 1) if i in text})
        if not nos:
            continue
        if code_only:
            sig = "\x1f".join(t.text for a, b in it.spans for t in src.tokens(code[a], code[b])
                               if t.kind != "com" and id(t) not in dropped)
        else:
            sig = "\n".join(src.raw[i - 1] for a, b in merged for i in range(a, b + 1))
        units.append(Unit(key, it.kind, it.label, [Line(i, text[i]) for i in nos], sig,
                          merged if it.blocks else None))
    return sorted(units, key=lambda u: (u.start, u.key))
