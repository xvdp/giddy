"""Julia adapter.

Blocks open with `function macro struct module baremodule begin let if for while try quote do`, or
`abstract type` / `primitive type`, at bracket depth 0, and close with `end` at bracket depth 0
(`a[end]` and comprehensions sit inside brackets).

Statement: tokens from a line start to the end of the line at bracket and block depth 0, unless
the line ends in a binary operator or comma; `;` also ends a statement.

Units
    function    `function` / `macro` blocks, after any `@macro` prefixes; short form
                `f(x) = ...`, `f(x)::T where T = ...`, `Base.f(x) = ...`
    class       `struct`, `mutable struct`, `abstract type`, `primitive type` (displayed whole);
                `module` / `baremodule` (header, `end`, loose statements; names inside get `M.`)
    module      remaining file-level statements

A string literal statement directly followed by a unit is its docstring: dropped in code mode,
included in the unit's span in raw mode.
"""
from __future__ import annotations

from .base import Adapter, Line, Unit
from .lexer import LexSpec, Tok
from .scoped import Item, ParseError, Source, build

LEX = LexSpec(line_comment=("#",), block_comment=("#=", "=#"), nested_block=True, single_quote="julia",
              backtick="cmd", multiline_str=True, julia=True)
OPENERS = {"function", "macro", "struct", "module", "baremodule", "begin", "let", "if", "for", "while",
           "try", "quote", "do"}
CONT_OPS = {"=", "+", "-", "*", "/", "\\", "^", "%", ",", "&&", "||", "|>", "->", "=>", "==", "!=", "<",
            ">", "<=", ">=", "<:", "::", "?", ":", ".", "|", "&", "+=", "-=", "*=", "/=", "..."}


class _Parser:
    def __init__(self, src: Source):
        self.src = src
        self.code = src.code
        self.match = src.match()
        self.items: list[Item] = []
        self.docs: list[Tok] = []
        self.ends = self.blocks()

    def is_opener(self, i: int) -> bool:
        code = self.code
        t = code[i]
        if t.kind != "id":
            return False
        if i and code[i - 1].text in (".", ":") and code[i - 1].end == t.start:
            return False                                    # x.begin, :function
        if t.text in ("abstract", "primitive"):
            return i + 1 < len(code) and code[i + 1].text == "type"
        return t.text in OPENERS

    def blocks(self) -> dict[int, int]:
        """opener index -> matching `end` index, for keywords at bracket depth 0."""
        code, out, stack, depth = self.code, {}, [], 0
        for i, t in enumerate(code):
            if t.kind == "op" and t.text in "([{":
                depth += 1
            elif t.kind == "op" and t.text in ")]}":
                depth -= 1
            elif depth == 0 and t.kind == "id":
                if t.text == "end" and not (i and code[i - 1].text in (".", ":") and code[i - 1].end == t.start):
                    if not stack:
                        raise ParseError(f"unmatched end at line {t.line}")
                    out[stack.pop()] = i
                elif self.is_opener(i):
                    stack.append(i)
        if stack:
            raise ParseError(f"unclosed {code[stack[-1]].text} at line {code[stack[-1]].line}")
        return out

    def statement_end(self, i: int, hi: int) -> int:
        code = self.code
        while True:
            t = code[i]
            if t.kind == "op" and t.text in "([{" and i in self.match:
                i = self.match[i]
            elif i in self.ends:
                i = self.ends[i]
            if code[i].text == ";" and code[i].kind == "op":
                return i
            nxt = i + 1
            if nxt >= hi:
                return i
            if code[nxt].line > code[i].eline and not (code[i].kind == "op" and code[i].text in CONT_OPS):
                return i
            i = nxt

    def walk(self, lo: int, hi: int, qual: str) -> list[tuple[int, int]]:
        code = self.code
        loose: list[tuple[int, int]] = []
        doc: Tok | None = None
        i = lo
        while i < hi:
            s, e = i, self.statement_end(i, hi)
            i = e + 1
            if s == e and code[s].kind == "str" and i < hi:
                doc = code[s]
                continue
            item = self.classify(s, e, qual)
            if item is None:
                if doc is not None:
                    loose.append((s - 1, s - 1))
                    doc = None
                loose.append((s, e))
                continue
            if doc is not None:
                self.docs.append(doc)
                item.lead.append(doc.line)
                doc = None
            self.items.append(item)
        if doc is not None:
            loose.append((hi - 1, hi - 1))
        return loose

    def classify(self, s: int, e: int, qual: str) -> Item | None:
        code = self.code
        k = s
        while k <= e and code[k].text == "@":                # @inline, Base.@propagate_inbounds
            k += 2
            while k <= e and code[k].text == "." and k + 1 <= e:
                k += 2
            if k <= e and code[k].text == "(" and code[k].start == code[k - 1].end:
                k = self.match[k] + 1
        if k > e:
            return None
        t = code[k]
        if t.text in ("function", "macro") and k in self.ends:
            j = k + 1
            stop = next((m for m in range(j, self.ends[k]) if code[m].text == "(" or
                         code[m].line > t.line), self.ends[k])
            if j < stop:
                if code[j].text == "(":                     # callable object: function (f::Foo)(x)
                    name = "".join(x.text for x in code[j:self.match[j] + 1])
                    paren = self.match[j] + 1 if self.match[j] + 1 < self.ends[k] else None
                else:
                    name = self.plain(j, stop)
                    paren = stop if code[stop].text == "(" else None
            else:
                name, paren = t.text, None
            if paren is not None and code[paren].text != "(":
                paren = None
            return self.item("function", qual + name, qual + name + "()", s, e, paren)
        if t.text == "mutable" and k + 1 <= e and code[k + 1].text == "struct":
            k += 1
            t = code[k]
        if t.text in ("struct", "abstract", "primitive") and (k in self.ends or k + 1 <= e):
            n = k + 1 if t.text == "struct" else k + 2
            name = code[n].text if n <= e else t.text
            kw = "struct" if t.text == "struct" else t.text + " type"
            return Item("class", qual + name, f"{kw} {qual + name}", [(s, e)])
        if t.text in ("module", "baremodule") and k in self.ends and k + 1 < e:
            name = code[k + 1].text
            end = self.ends[k]
            stmts = self.walk(k + 2, end, qual + name + ".")
            return Item("class", qual + name, f"module {qual + name}", [(s, k + 1), (end, end)] + stmts,
                        blocks=True)
        return self.short_function(k, e, qual)

    def short_function(self, k: int, e: int, qual: str) -> Item | None:
        """`name(args)[::T][ where ...] = body` at depth 0."""
        code = self.code
        j = k
        if code[j].kind != "id":
            return None
        while j + 2 <= e and code[j + 1].text == "." and code[j + 2].kind == "id":
            j += 2
        name_end = j
        if j + 1 <= e and code[j + 1].text == "{":
            j = self.match[j + 1]
        if j + 1 > e or code[j + 1].text != "(" or code[j + 1].start != code[j].end:
            return None
        paren = j + 1
        m = self.match[paren] + 1
        if m > e or code[m].text not in ("=", "::", "where"):
            return None
        while m <= e and not (code[m].kind == "op" and code[m].text == "="):
            m = self.match[m] + 1 if code[m].text in "([{" and m in self.match else m + 1
        if m > e:
            return None
        name = qual + self.plain(k, name_end + 1)
        return self.item("function", name, name + "()", k, e, paren)

    def plain(self, a: int, b: int) -> str:
        out, depth = "", 0
        for t in self.code[a:b]:
            depth += (t.text == "{") - (t.text == "}")
            if depth == 0 and t.text != "}":
                out += t.text
        return out

    def item(self, kind: str, name: str, label: str, s: int, e: int, paren: int | None) -> Item:
        params = ""
        if paren is not None and paren in self.match:
            params = " ".join(t.text for t in self.code[paren + 1:self.match[paren]])
        return Item(kind, name, label, [(s, e)], params)


class JuliaAdapter(Adapter):
    name = "julia"
    fence = "julia"

    def units(self, source: str, code_only: bool) -> list[Unit] | None:
        try:
            src = Source(source, LEX)
            p = _Parser(src)
            loose = p.walk(0, len(src.code), "")
        except (ParseError, RecursionError, IndexError, KeyError):
            return None
        items = p.items + [Item("module", "<module>", "module", loose, blocks=True)]
        return build(src, items, code_only, p.docs)

    def code(self, source: str, code_only: bool) -> list[Line]:
        src = Source(source, LEX)
        drop: list[Tok] = []
        try:
            p = _Parser(src)
            p.walk(0, len(src.code), "")
            drop = p.docs
        except (ParseError, RecursionError, IndexError, KeyError):
            pass
        return [Line(i, t) for i, t in sorted(src.normalized(code_only, drop).items())]
