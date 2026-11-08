"""Configurable tokenizer for C-family languages, Rust, JavaScript/TypeScript, GLSL, Slang and Julia.

The tokenizer only needs to separate code from comments and string literals and to expose brackets,
identifiers and operators for scope detection. It does not validate syntax.

Token kinds
    id    identifier or keyword (Rust lifetimes `'a` included)
    num   numeric literal
    str   string, character, template, regex or command literal (interpolations included)
    com   comment (doc comments included)
    pre   preprocessor line, continuation lines included
    op    operator or punctuation

Input must use `\\n` line endings; `Source` normalizes them.
"""
from __future__ import annotations

import unicodedata
from bisect import bisect_right
from dataclasses import dataclass

OPS = ("...", "===", "!==", "**=", "=>", "->", "::", "==", "!=", "<=", ">=", "&&", "||", "++", "--",
       "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "..", "?.", "??", "|>", "<:")
# `<<` and `>>` stay two tokens so generic argument lists (`Vec<Vec<u8>>`) keep balanced angles.

REGEX_AFTER = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
               "do", "else", "yield", "await"}


@dataclass(frozen=True)
class LexSpec:
    line_comment: tuple[str, ...] = ("//",)
    block_comment: tuple[str, str] | None = ("/*", "*/")
    nested_block: bool = False          # Rust and Julia block comments nest
    preprocessor: bool = False          # `#` at line start opens a directive (C, C++, GLSL, Slang)
    single_quote: str = "char"          # char | string | rust | julia
    backtick: str = ""                  # "" | template (JS/TS) | cmd (Julia)
    regex: bool = False                 # JS/TS regex literals
    multiline_str: bool = False         # "..." may span lines (Rust, Julia)
    raw_strings: str = ""               # "" | cpp (R"d(...)d") | rust (r#"..."#)
    julia: bool = False                 # triple quotes, `$(...)` interpolation, `!` in identifiers


@dataclass(frozen=True)
class Tok:
    kind: str
    text: str
    start: int
    end: int
    line: int                           # 1-based line of the first character
    eline: int                          # line of the last character


class LexError(ValueError):
    pass


def _ident_start(c: str) -> bool:
    return c.isalpha() or c == "_" or (ord(c) > 127 and c.isidentifier())


def _ident_char(c: str) -> bool:
    return c.isalnum() or c == "_" or (ord(c) > 127 and ("a" + c).isidentifier())


def _julia_char(c: str) -> bool:
    """Julia identifiers also take primes, sub/superscripts and modifier letters: `b′`, `x₀`, `uᵢ`."""
    return _ident_char(c) or c in "′″‴⁗" or (ord(c) > 127 and unicodedata.category(c) in ("No", "Lm", "Sk", "Mn", "Mc", "Me"))


class _Lexer:
    def __init__(self, src: str, spec: LexSpec):
        self.src = src
        self.n = len(src)
        self.spec = spec
        self.starts = [0] + [i + 1 for i, c in enumerate(src) if c == "\n"]
        self.toks: list[Tok] = []

    def line(self, pos: int) -> int:
        return bisect_right(self.starts, pos)

    def emit(self, kind: str, start: int, end: int, out: list[Tok] | None) -> None:
        if out is not None:
            out.append(Tok(kind, self.src[start:end], start, end, self.line(start), self.line(max(start, end - 1))))

    def prev(self, out: list[Tok] | None) -> Tok | None:
        if not out:
            return None
        for t in reversed(out):
            if t.kind != "com":
                return t
        return None

    # -- literal scanners: each takes the index of the opening delimiter, returns the index past the end
    def quoted(self, i: int, q: str, multiline: bool) -> int:
        src, n = self.src, self.n
        j = i + 1
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == q:
                return j + 1
            if c == "\n" and not multiline:
                return j
            j += 1
        return n

    def julia_str(self, i: int) -> int:
        src, n = self.src, self.n
        triple = src.startswith('"""', i)
        j = i + (3 if triple else 1)
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "$" and j + 1 < n and src[j + 1] == "(":
                j = self.run(j + 2, ")", [])
                continue
            if triple and src.startswith('"""', j):
                return j + 3
            if not triple and c == '"':
                return j + 1
            j += 1
        return n

    def template(self, i: int) -> int:
        src, n = self.src, self.n
        j = i + 1
        while j < n:
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1
            if c == "$" and j + 1 < n and src[j + 1] == "{":
                j = self.run(j + 2, "}", [])
                continue
            j += 1
        return n

    def regex(self, i: int) -> int | None:
        src, n = self.src, self.n
        j, cls = i + 1, False
        while j < n:
            c = src[j]
            if c == "\n":
                return None
            if c == "\\":
                j += 2
                continue
            if c == "[":
                cls = True
            elif c == "]":
                cls = False
            elif c == "/" and not cls:
                j += 1
                while j < n and src[j].isalpha():
                    j += 1
                return j
            j += 1
        return None

    def block_comment(self, i: int) -> int:
        open_, close = self.spec.block_comment  # type: ignore[misc]
        src, n = self.src, self.n
        depth, j = 1, i + len(open_)
        while j < n and depth:
            if self.spec.nested_block and src.startswith(open_, j):
                depth += 1
                j += len(open_)
            elif src.startswith(close, j):
                depth -= 1
                j += len(close)
            else:
                j += 1
        return j

    def raw_cpp(self, i: int) -> int:
        """`i` at the opening quote of R"delim( ... )delim"."""
        src = self.src
        k = src.find("(", i)
        if k < 0 or k - i > 17:
            return self.quoted(i, '"', False)
        end = src.find(")" + src[i + 1:k] + '"', k)
        return self.n if end < 0 else end + len(src[i + 1:k]) + 2

    def raw_rust(self, i: int) -> int:
        """`i` at the first `#` or `"` after the r / br prefix."""
        src = self.src
        k = i
        while k < self.n and src[k] == "#":
            k += 1
        if k >= self.n or src[k] != '"':
            return i
        end = src.find('"' + "#" * (k - i), k + 1)
        return self.n if end < 0 else end + 1 + (k - i)

    # -- main loop ------------------------------------------------------------------------------
    def run(self, i: int, stop: str | None, out: list[Tok] | None) -> int:
        """Tokenize from `i`. With `stop`, return past the first unbalanced `stop` character."""
        src, n, spec = self.src, self.n, self.spec
        depth = 0
        bol = True
        while i < n:
            c = src[i]
            if c == "\n":
                bol = True
                i += 1
                continue
            if c in " \t\r\f\v":
                i += 1
                continue
            start = i
            was_bol, bol = bol, False
            if stop is not None:
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    if depth == 0 and c == stop:
                        return i + 1
                    depth -= 1
            if spec.preprocessor and c == "#" and was_bol:
                j = i
                while j < n:
                    e = src.find("\n", j)
                    e = n if e < 0 else e
                    if src[j:e].rstrip().endswith("\\"):
                        j = e + 1
                        continue
                    j = e
                    break
                self.emit("pre", start, j, out)
                i = j
                continue
            if any(src.startswith(lc, i) for lc in spec.line_comment) and \
                    not (spec.julia and src.startswith("#=", i)):
                e = src.find("\n", i)
                i = n if e < 0 else e
                self.emit("com", start, i, out)
                continue
            if spec.block_comment and src.startswith(spec.block_comment[0], i):
                i = self.block_comment(i)
                self.emit("com", start, i, out)
                continue
            if c == '"':
                i = self.julia_str(i) if spec.julia else self.quoted(i, '"', spec.multiline_str)
                self.emit("str", start, i, out)
                continue
            if c == "'":
                i = self.single(i, out)
                continue
            if c == "`" and spec.backtick:
                i = self.template(i) if spec.backtick == "template" else self.quoted(i, "`", True)
                self.emit("str", start, i, out)
                continue
            if c == "/" and spec.regex:
                p = self.prev(out) if out is not None else None
                if p is None or (p.kind == "op" and p.text not in (")", "]", "}")) or \
                        (p.kind == "id" and p.text in REGEX_AFTER):
                    j = self.regex(i)
                    if j is not None:
                        self.emit("str", start, j, out)
                        i = j
                        continue
            if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
                j = i + 1
                while j < n:
                    d = src[j]
                    if d == "." and src.startswith("..", j):
                        break
                    if d in "+-" and src[j - 1] in "eEpP" and not src[start:j].lower().startswith("0x"):
                        j += 1
                        continue
                    if not (_ident_char(d) or d == "."):
                        break
                    j += 1
                self.emit("num", start, j, out)
                i = j
                continue
            if _ident_start(c):
                j = i + 1
                cont = _julia_char if spec.julia else _ident_char
                while j < n and (cont(src[j]) or (spec.julia and src[j] == "!" and src[j + 1:j + 2] != "=")):
                    j += 1
                i = self.after_ident(start, j, out)
                continue
            op = next((o for o in OPS if src.startswith(o, i)), c)
            i += len(op)
            self.emit("op", start, i, out)
        return n

    def single(self, i: int, out: list[Tok] | None) -> int:
        src, n, mode = self.src, self.n, self.spec.single_quote
        if mode == "string":
            j = self.quoted(i, "'", False)
        elif mode == "rust":
            if i + 1 < n and src[i + 1] == "\\" or (i + 2 < n and src[i + 2] == "'"):
                j = self.quoted(i, "'", False)
            else:                                   # lifetime or loop label
                j = i + 1
                while j < n and _ident_char(src[j]):
                    j += 1
                self.emit("id", i, j, out)
                return j
        elif mode == "julia":
            p = self.prev(out) if out is not None else None
            if p is not None and p.end == i and (p.kind in ("id", "num", "str") or p.text in (")", "]", "}", "'", ".")):
                self.emit("op", i, i + 1, out)      # adjoint operator
                return i + 1
            j = self.quoted(i, "'", False)
        else:
            j = self.quoted(i, "'", False)
        self.emit("str", i, j, out)
        return j

    def after_ident(self, start: int, j: int, out: list[Tok] | None) -> int:
        """Identifier ending at `j`; absorbs a string prefix (R"..", r#".."#, b'.', u8"..", raw"..")."""
        src, n, spec = self.src, self.n, self.spec
        word = src[start:j]
        nxt = src[j] if j < n else ""
        if spec.raw_strings == "cpp" and nxt == '"' and word in ("R", "u8R", "uR", "UR", "LR"):
            end = self.raw_cpp(j)
        elif spec.raw_strings == "cpp" and nxt in "\"'" and nxt and word in ("u8", "u", "U", "L"):
            end = self.quoted(j, nxt, False)
        elif spec.raw_strings == "rust" and word in ("r", "br") and nxt in '#"' and nxt:
            end = self.raw_rust(j)
            if end == j:
                self.emit("id", start, j, out)
                return j
        elif spec.raw_strings == "rust" and word == "b" and nxt in "\"'" and nxt:
            end = self.quoted(j, nxt, nxt == '"')
        elif spec.julia and nxt == '"':
            end = self.julia_str(j) if word not in ("raw", "r") else self.quoted(j, '"', True)
        else:
            self.emit("id", start, j, out)
            return j
        self.emit("str", start, end, out)
        return end


def tokenize(src: str, spec: LexSpec) -> list[Tok]:
    lx = _Lexer(src, spec)
    out: list[Tok] = []
    lx.run(0, None, out)
    return out
