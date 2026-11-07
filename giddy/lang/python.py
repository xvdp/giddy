"""Python adapter on the standard-library `ast` and `tokenize` modules.

Units
    function   outermost `def` / `async def` reached through classes only. A closure belongs to the
               function that encloses it; a method `m` of class `A` nested in `B` has key `B.A.m`.
               Span: first decorator line to `end_lineno`.
    class      class header (decorators, bases) plus every body statement that is not a function or
               class. Key: qualified class name.
    module     module-level statements that are not functions or classes. Key: `<module>`.

Repeated keys (property getter and setter, conditional redefinition) get `#2`, `#3` suffixes in
source order.

Code-only mode (default)
    signature   `ast.dump` without positions, docstrings removed at every depth. Comments, blank
                lines and formatting do not reach the AST, so a unit changed only in docstrings,
                comments or layout compares equal and is dropped.
    lines       docstring lines and comments removed, trailing whitespace stripped.
Raw mode
    signature   unit source text; lines   original text with trailing whitespace stripped.
"""
from __future__ import annotations

import ast
import copy
import io
import tokenize
from typing import Sequence, Union

from .base import Adapter, Line, Unit, split_lines

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
_Def = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
_SCOPE = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring(node: ast.AST) -> ast.Expr | None:
    body = getattr(node, "body", None)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0]
    return None


def _strip_docstrings(node: ast.AST) -> ast.AST:
    """Deep copy of `node` with docstrings removed from every module, class and function in it."""
    node = copy.deepcopy(node)
    for sub in ast.walk(node):
        if isinstance(sub, _SCOPE) and _docstring(sub) is not None:
            sub.body = sub.body[1:] or [ast.Pass()]
    return node


def _dump(nodes: Sequence[ast.AST]) -> str:
    return "\n".join(ast.dump(n, include_attributes=False) for n in nodes)


def _start(node: ast.stmt) -> int:
    decos = getattr(node, "decorator_list", None) or []
    return min([node.lineno] + [d.lineno for d in decos])


def _end(node: ast.stmt) -> int:
    return node.end_lineno or node.lineno


class PythonAdapter(Adapter):
    name = "python"
    fence = "python"

    def units(self, source: str, code_only: bool) -> list[Unit] | None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return None
        raw = split_lines(source)
        text = self._normalize(source, raw, tree) if code_only else \
            {i: t.rstrip() for i, t in enumerate(raw, 1)}
        walker = _Walker(raw, text, code_only)
        walker.container("<module>", "module", "module", None, tree.body, "", tree)
        return sorted(walker.units, key=lambda u: (u.start, u.key))

    def code(self, source: str, code_only: bool) -> list[Line]:
        if not code_only:
            return super().code(source, code_only)
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return super().code(source, code_only)
        text = self._normalize(source, split_lines(source), tree)
        return [Line(i, t) for i, t in sorted(text.items())]

    @staticmethod
    def _normalize(source: str, raw: list[str], tree: ast.Module) -> dict[int, str]:
        """Line number -> code text with docstrings and comments removed; dropped lines absent."""
        text = {i: t for i, t in enumerate(raw, 1)}
        for node in ast.walk(tree):
            doc = _docstring(node) if isinstance(node, _SCOPE) else None
            if doc is None:
                continue
            end = _end(doc)
            first = raw[doc.lineno - 1].encode()
            last = raw[end - 1].encode()
            alone = not first[:doc.col_offset].strip() and not last[doc.end_col_offset or 0:].strip(b" \t;")
            if alone:   # docstring sharing a line with code stays
                for i in range(doc.lineno, end + 1):
                    text.pop(i, None)
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type == tokenize.COMMENT and tok.start[0] in text:
                    row, col = tok.start
                    head = text[row][:col]
                    if head.strip():
                        text[row] = head
                    else:
                        del text[row]
        except (tokenize.TokenError, SyntaxError):
            pass
        return {i: t.rstrip() for i, t in text.items()}


class _Walker:
    def __init__(self, raw: list[str], text: dict[int, str], code_only: bool):
        self.raw = raw
        self.text = text
        self.code_only = code_only
        self.units: list[Unit] = []
        self._seen: dict[str, int] = {}

    def _key(self, key: str) -> str:
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        return key if n == 1 else f"{key}#{n}"

    def _lines(self, spans: list[tuple[int, int]]) -> list[Line]:
        nos = sorted({i for a, b in spans for i in range(a, b + 1) if i in self.text})
        return [Line(i, self.text[i]) for i in nos]

    def _source(self, spans: list[tuple[int, int]]) -> str:
        nos = sorted({i for a, b in spans for i in range(a, b + 1)})
        return "\n".join(self.raw[i - 1] for i in nos)

    def function(self, node: _Def, qual: str) -> None:
        span = (_start(node), _end(node))
        sig = _dump([_strip_docstrings(node)]) if self.code_only else self._source([span])
        key = self._key(qual + node.name)
        self.units.append(Unit(key, "function", key.split("#")[0] + "()", self._lines([span]), sig))

    def container(self, key: str, kind: str, label: str, header: tuple[int, int] | None,
                  body: list[ast.stmt], qual: str, node: ast.AST) -> None:
        loose = []
        doc = _docstring(node) if self.code_only else None
        for stmt in body:
            if isinstance(stmt, _FUNC):
                self.function(stmt, qual)
            elif isinstance(stmt, ast.ClassDef):
                cq = qual + stmt.name
                first = stmt.body[0].lineno
                h_end = first - 1 if first > stmt.lineno else stmt.lineno
                self.container(self._key(cq), "class", f"class {cq}", (_start(stmt), h_end),
                               stmt.body, cq + ".", stmt)
            elif stmt is not doc:
                loose.append(stmt)

        blocks: list[tuple[int, int]] = []
        for a, b in sorted(([header] if header else []) + [(_start(s), _end(s)) for s in loose]):
            if blocks and a <= blocks[-1][1]:     # `class A: x = 1`, `a = 1; b = 2` share a line
                blocks[-1] = (blocks[-1][0], max(b, blocks[-1][1]))
            else:
                blocks.append((a, b))
        lines = self._lines(blocks)
        if not lines:
            return
        if self.code_only:
            head = []
            if isinstance(node, ast.ClassDef):
                shell = copy.copy(node)
                shell.body = []
                head = [shell]
            sig = _dump(head + loose)
        else:
            sig = self._source(blocks)
        self.units.append(Unit(key, kind, label, lines, sig, blocks))
