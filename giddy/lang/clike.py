"""Brace-language adapter: C, C++, CUDA, Rust, TypeScript, JavaScript, GLSL, Slang/HLSL.

Structural parse over code tokens (comments excluded), one container body at a time:

    statement   tokens from the previous boundary to `;`, to a closing `}`, to a preprocessor line,
                or (JS/TS) to a line break that automatic semicolon insertion would end
    `{` at bracket depth 0 is classified by its header (statement tokens before it):
        container   leading keyword in `containers` (class, struct, namespace, impl, trait, ...);
                    recursed; header, non-function statements and closing brace form a class unit
        function    Rust `fn`; else a parameter group `(...)` preceded by a name that is not a control
                    keyword; else `name = (...) =>`, `name = function`, `name = [...](...)` (lambda)
        otherwise   data block (initializer, enum, object literal) inside a loose statement

Units: functions (whole, nested closures included), containers (class units), file-level loose
statements (`<module>`). Names are qualified with `::` (C++, Rust) or `.` (others).
Unbalanced brackets (unusual preprocessor branching, unsupported syntax) raise ParseError: line diff.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import Adapter, Line, Unit
from .lexer import LexSpec, Tok
from .scoped import Item, ParseError, Source, build

CONTROL = {"if", "for", "while", "switch", "catch", "with", "foreach", "do", "else", "return", "try",
           "sizeof", "typeof", "defined", "match", "loop", "until", "elif", "new", "await", "yield"}
SKIP_GROUPS = {"layout", "register", "packoffset", "alignas", "__declspec", "__attribute__", "decltype",
               "noexcept", "throw", "requires"}
MODIFIERS = {"export", "default", "declare", "abstract", "public", "private", "protected", "static",
             "final", "sealed", "readonly", "async", "pub", "unsafe", "inline", "constexpr", "consteval",
             "virtual", "explicit", "friend", "internal", "override", "__device__", "__host__",
             "__global__", "__forceinline__", "__inline", "__inline__", "partial", "mutable", "const"}
# C family: identifiers allowed between a parameter list and the function body.
POST_QUALS = {"const", "volatile", "noexcept", "override", "final", "mutable", "throw", "requires", "try",
              "constexpr", "__attribute__", "where", "async", "sealed", "PURE", "MUST_USE_RESULT"}
ACCESS = {"public", "private", "protected", "signals", "slots"}
# JS/TS: a line break does not end the statement when the next line starts with one of these.
CONTINUE_NEXT = {".", "?.", ")", "]", "}", ",", ":", "?", "=>", "&&", "||", "??", "+", "*", "/", "%",
                 "=", "==", "===", "!=", "!==", "<", ">", "<=", ">=", "|", "&", "^", "instanceof", "in",
                 "as", "satisfies", "extends", "implements", "from"}
NO_ASI_AFTER = {")", "]", "}", "++", "--"}


@dataclass(frozen=True)
class CSpec:
    name: str
    fence: str
    lex: LexSpec
    sep: str = "."
    containers: frozenset = frozenset()
    attributes: bool = False            # leading `[...]` groups are attributes (C++ [[..]], Slang [..])
    asi: bool = False
    rust: bool = False
    ts: bool = False
    cpp: bool = False


C_LEX = LexSpec(preprocessor=True, raw_strings="cpp")
SPECS = {
    "c": CSpec("c", "c", C_LEX, ".", frozenset({"struct", "union"})),
    "cpp": CSpec("cpp", "cpp", C_LEX, "::", frozenset({"class", "struct", "union", "namespace", "extern"}),
                 attributes=True, cpp=True),
    "rust": CSpec("rust", "rust", LexSpec(nested_block=True, single_quote="rust", multiline_str=True,
                                          raw_strings="rust"),
                  "::", frozenset({"impl", "trait", "mod", "extern"}), rust=True),
    "ts": CSpec("ts", "ts", LexSpec(single_quote="string", backtick="template", regex=True), ".",
                frozenset({"class", "interface", "namespace", "module", "global"}), asi=True, ts=True),
    "js": CSpec("js", "js", LexSpec(single_quote="string", backtick="template", regex=True), ".",
                frozenset({"class"}), asi=True),
    "glsl": CSpec("glsl", "glsl", LexSpec(preprocessor=True), ".", frozenset({"struct"})),
    "slang": CSpec("slang", "hlsl", LexSpec(preprocessor=True), ".",
                   frozenset({"struct", "class", "interface", "extension", "namespace"}), attributes=True),
}


def _text(toks: list[Tok]) -> str:
    out = ""
    for t in toks:
        if out and (t.kind in ("id", "num", "str") and (out[-1].isalnum() or out[-1] in "_>*&]") or out[-1] == ","):
            out += " "
        out += t.text
    return out


@dataclass
class _Parser:
    spec: CSpec
    src: Source
    items: list[Item] = field(default_factory=list)

    def __post_init__(self):
        self.code = self.src.code
        self.match = self.src.match()

    # -- header helpers -------------------------------------------------------------------------
    def top(self, a: int, b: int) -> list[int]:
        """Indices of depth-0 tokens in code[a:b]; a bracket group appears as its opening index."""
        out, i = [], a
        while i < b:
            out.append(i)
            t = self.code[i]
            i = self.match[i] + 1 if t.kind == "op" and t.text in "([{" and i in self.match else i + 1
        return out

    def strip(self, idx: list[int]) -> list[int]:
        """Drop leading attributes, decorators, template headers, modifiers and preprocessor lines."""
        code, k = self.code, 0
        while k < len(idx):
            t = code[idx[k]]
            if t.kind == "pre":
                k += 1
            elif self.spec.rust and t.text == "#" and k + 1 < len(idx):
                k += 2 if code[idx[k + 1]].text == "[" else 3      # #[..] or #![..]
            elif self.spec.attributes and t.text == "[":
                k += 1
            elif t.text == "@" and k + 1 < len(idx):               # decorator @a.b(...)
                k += 2
                while k < len(idx) and code[idx[k]].text in (".",) and k + 1 < len(idx):
                    k += 2
                if k < len(idx) and code[idx[k]].text == "(":
                    k += 1
            elif t.text == "template" and k + 1 < len(idx) and code[idx[k + 1]].text == "<":
                k = self.angle_end(idx, k + 1) + 1
            elif t.kind == "id" and t.text in MODIFIERS:
                k += 1
                if t.text == "pub" and k < len(idx) and code[idx[k]].text == "(":
                    k += 1
            else:
                break
        return idx[k:]

    def angle_end(self, idx: list[int], k: int) -> int:
        """Position in `idx` of the `>` closing the `<` at position k."""
        depth = 0
        for j in range(k, len(idx)):
            s = self.code[idx[j]].text
            depth += (s == "<") - (s == ">")
            if depth == 0:
                return j
        return len(idx) - 1

    def angle_start(self, idx: list[int], k: int) -> int:
        """Position in `idx` of the `<` opening the `>` at position k."""
        depth = 0
        for j in range(k, -1, -1):
            s = self.code[idx[j]].text
            depth += (s == ">") - (s == "<")
            if depth == 0:
                return j
        return 0

    def name_before(self, idx: list[int], k: int) -> str | None:
        """Qualified name ending just before position k (a `(` or `=`), or None."""
        code = self.code
        j = k - 1
        if j >= 0 and code[idx[j]].text == ">" and (self.spec.cpp or self.spec.ts or self.spec.name == "slang"):
            j = self.angle_start(idx, j) - 1
        if j < 0:
            return None
        ops = [i for i in range(j + 1) if code[idx[i]].text == "operator"]
        if self.spec.cpp and ops:
            o = ops[-1]
            name = "operator" + "".join("()" if code[idx[i]].text == "(" else code[idx[i]].text
                                        for i in range(o + 1, j + 1))
            return self.qualify(idx, o - 1, name)
        t = code[idx[j]]
        if t.kind != "id":
            return None
        return self.qualify(idx, j - 1, t.text)

    def qualify(self, idx: list[int], j: int, name: str) -> str:
        code = self.code
        if j >= 0 and code[idx[j]].text == "~":
            name, j = "~" + name, j - 1
        while j >= 1 and code[idx[j]].text in ("::", ".") and code[idx[j - 1]].kind in ("id", "op"):
            if code[idx[j - 1]].text == ">":
                j = self.angle_start(idx, j - 1)
            prev = code[idx[j - 1]]
            if prev.kind != "id":
                break
            name, j = prev.text + self.spec.sep + name, j - 2
        return name

    # -- classification -------------------------------------------------------------------------
    def classify(self, s: int, brace: int) -> tuple[str, str, str, str]:
        """(kind, name, label, params) for the `{` at `brace` with statement start `s`."""
        code, spec = self.code, self.spec
        idx = self.strip([i for i in self.top(s, brace) if code[i].kind != "pre"])
        words = [code[i].text for i in idx if code[i].kind == "id"]
        if not idx:
            return "data", "", "", ""
        first = code[idx[0]]

        if spec.rust:
            if "fn" in words:
                k = next(p for p, i in enumerate(idx) if code[i].text == "fn")
                name = code[idx[k + 1]].text if k + 1 < len(idx) else "fn"
                paren = next((i for i in idx[k:] if code[i].text == "("), None)
                return "function", name, name + "()", self.params(paren)
            if first.text in spec.containers:
                return ("container",) + self.rust_container(idx)
            return "data", "", "", ""

        if first.kind == "id" and first.text in spec.containers:
            kw = first.text
            if kw == "extern":
                if len(idx) == 2 and code[idx[1]].kind == "str":
                    return "container", "", f"extern {code[idx[1]].text}", ""
            else:
                parens = [p for p, i in enumerate(idx) if code[i].text == "("]
                cut = next((p for p, i in enumerate(idx) if code[i].text in (":", "extends", "implements")),
                           len(idx))
                if not parens or parens[0] > cut:
                    name = self.container_name(idx[1:cut])
                    return "container", name, f"{kw} {name}", ""

        # arrow functions, function expressions, lambdas assigned to a name
        eq = next((p for p, i in enumerate(idx) if code[i].text == "=" and code[i].kind == "op"), None)
        if eq is not None:
            tail = [code[i].text for i in idx[eq + 1:]]
            if (tail and tail[-1] == "=>") or "function" in tail or (spec.cpp and "]" in tail):
                name = self.name_before(idx, eq) or "(anonymous)"
                paren = next((i for i in idx[eq + 1:] if code[i].text == "("), None)
                return "function", name, name + "()", self.params(paren)
            return "data", "", "", ""
        if code[idx[-1]].text == "=>":                              # `x => {` in a class field
            return "data", "", "", ""

        parens = [p for p, i in enumerate(idx) if code[i].text == "("]
        if not parens:
            return "data", "", "", ""
        colon = next((p for p, i in enumerate(idx) if code[i].text == ":" and p > parens[0]), None)
        cand = [p for p in parens if colon is None or p < colon]
        for p in reversed(cand):
            prev = code[idx[p - 1]].text if p else ""
            if prev in SKIP_GROUPS:
                continue
            if prev in CONTROL or first.text in CONTROL:
                return "data", "", "", ""
            if prev == "function" or (spec.ts or spec.name == "js") and prev in ("default", "async"):
                name = "(anonymous)"
            else:
                name = self.name_before(idx, p)
            if name is None or not self.signature_tail(idx[p + 1:colon]):
                return "data", "", "", ""
            return "function", name, name + "()", self.params(idx[p])
        return "data", "", "", ""

    def signature_tail(self, tail: list[int]) -> bool:
        """C family: tokens between `)` and `{` must be qualifiers or a trailing return type, so a macro
        call without `;` (`DECLARE(x)` then `typedef struct {`) is not taken for a parameter list."""
        if self.spec.ts or self.spec.name == "js":
            return True
        for i in tail:
            t = self.code[i]
            if t.text == "->":
                return True
            if t.kind == "id" and t.text not in POST_QUALS:
                return False
        return True

    def params(self, paren: int | None) -> str:
        if paren is None or paren not in self.match:
            return ""
        return _text(self.code[paren + 1:self.match[paren]])

    def container_name(self, idx: list[int]) -> str:
        code = self.code
        strs = [code[i].text for i in idx if code[i].kind == "str"]
        stop = next((p for p, i in enumerate(idx) if code[i].text in ("<", "final", "where", "{")), len(idx))
        ids = [p for p in range(stop) if code[idx[p]].kind == "id"]
        if not ids:
            return strs[0] if strs else "(anonymous)"
        p = ids[-1]
        name = self.qualify(idx, p - 1, code[idx[p]].text) if p else code[idx[p]].text
        if stop < len(idx) and code[idx[stop]].text == "<":
            name += "".join(code[i].text for i in idx[stop:self.angle_end(idx, stop) + 1])
        return name

    def rust_container(self, idx: list[int]) -> tuple[str, str, str]:
        code = self.code
        kw = code[idx[0]].text
        rest = idx[1:]
        if rest and code[rest[0]].text == "<":
            rest = rest[self.angle_end(rest, 0) + 1:]
        rest = rest[:next((p for p, i in enumerate(rest) if code[i].text == "where"), len(rest))]

        def plain(part: list[int]) -> str:
            out, depth = "", 0
            for i in part:
                s = code[i].text
                depth += (s == "<") - (s == ">")
                if depth == 0 and s != ">":
                    out += s
            return out

        if kw == "extern":
            return "", "extern " + " ".join(code[i].text for i in rest), ""
        if kw == "impl":
            f = next((p for p, i in enumerate(rest) if code[i].text == "for"), None)
            if f is not None:
                name = f"<{plain(rest[f + 1:])} as {plain(rest[:f])}>"
                return name, f"impl {plain(rest[:f])} for {plain(rest[f + 1:])}", ""
            name = plain(rest)
            return name, f"impl {name}", ""
        name = code[rest[0]].text if rest else "(anonymous)"
        return name, f"{kw} {name}", ""

    # -- body walk ------------------------------------------------------------------------------
    def ends_line(self, last: int, hi: int, s: int) -> bool:
        """JS/TS: automatic semicolon insertion ends the statement after token `last`."""
        code = self.code
        if not self.spec.asi or last + 1 >= hi or code[last + 1].line <= code[last].eline:
            return False
        t, nxt = code[last], code[last + 1]
        if code[s].text == "@":
            return False
        if t.kind == "op" and t.text not in NO_ASI_AFTER:
            return False
        return nxt.text not in CONTINUE_NEXT

    def body(self, lo: int, hi: int, qual: str) -> list[tuple[int, int]]:
        """Walk code[lo:hi]; register functions and containers; return loose statement spans."""
        code, spec, match = self.code, self.spec, self.match
        loose: list[tuple[int, int]] = []
        i = s = lo
        while i < hi:
            t = code[i]
            if t.kind == "pre" and s == i:
                loose.append((i, i))
                i = s = i + 1
                continue
            if spec.rust and s == i and t.text == "#" and i + 2 < hi and code[i + 1].text == "!":
                end = match.get(i + 2, i + 2)
                loose.append((i, end))
                i = s = end + 1
                continue
            if spec.cpp and t.text in ACCESS and i + 1 < hi and code[i + 1].text == ":" and s == i:
                loose.append((i, i + 1))
                i = s = i + 2
                continue
            if t.kind == "op" and t.text in "([" and i in match:
                last = match[i]
            elif t.kind == "op" and t.text == "{":
                close = match[i]
                prev = code[i - 1].text if i > s else ""
                if (spec.ts and self.type_brace(s, i)) or (spec.cpp and self.brace_init(s, i)):
                    i = close + 1                                   # type literal / brace initializer
                    if self.ends_line(close, hi, s):
                        loose.append((s, close))
                        s = i
                    continue
                kind, name, label, params = self.classify(s, i)
                end = close + 1 if close + 1 < hi and code[close + 1].text == ";" else close
                if kind == "container":
                    inner = qual + name + spec.sep if name else qual
                    stmts = self.body(i + 1, close, inner)
                    self.items.append(Item("class", qual + name if name else label, label,
                                           [(s, i), (close, end)] + stmts, blocks=True))
                elif kind == "function":
                    self.items.append(Item("function", qual + name, qual + label, [(s, end)], params))
                else:
                    j = close + 1
                    if j < hi and code[j].text == ";":
                        loose.append((s, j))
                        i = s = j + 1
                        continue
                    if j < hi and code[j].line == code[close].eline and code[j].text != "}" \
                            and not (code[j].kind == "pre"):
                        i = j
                        continue
                    loose.append((s, close))
                    i = s = j
                    continue
                i = s = end + 1
                continue
            elif t.kind == "op" and t.text == ";":
                loose.append((s, i))
                i = s = i + 1
                continue
            else:
                last = i
            if self.ends_line(last, hi, s):
                loose.append((s, last))
                s = last + 1
            i = last + 1
        if s < hi:
            loose.append((s, hi - 1))
        return loose

    def type_brace(self, s: int, brace: int) -> bool:
        """TS: `{` opening an object type (`): { a: T } {`, `Promise<{ a: T }>`, `A & { b: U }`)."""
        code = self.code
        if brace > s and code[brace - 1].text in (":", "|", "&", "<"):
            return True
        idx = self.top(s, brace)
        return sum((code[i].text == "<") - (code[i].text == ">") for i in idx) > 0

    def brace_init(self, s: int, brace: int) -> bool:
        """C++ constructor initializer `: a{1}, b{2} {`: a `{` right after `name` that follows `:` or `,`
        in a header holding a parameter group before the colon."""
        code = self.code
        if brace - s < 3 or code[brace - 1].kind != "id" or code[brace - 2].text not in (":", ","):
            return False
        idx = self.top(s, brace)
        colon = next((p for p, i in enumerate(idx) if code[i].text == ":"), None)
        return colon is not None and any(code[i].text == "(" for i in idx[:colon])


class CLikeAdapter(Adapter):
    def __init__(self, spec: CSpec):
        self.spec = spec
        self.name = spec.name
        self.fence = spec.fence

    def units(self, source: str, code_only: bool) -> list[Unit] | None:
        try:
            src = Source(source, self.spec.lex)
            p = _Parser(self.spec, src)
            loose = p.body(0, len(src.code), "")
        except (ParseError, RecursionError, StopIteration, IndexError):
            return None
        items = p.items + [Item("module", "<module>", "module", loose, blocks=True)]
        return build(src, items, code_only)

    def code(self, source: str, code_only: bool) -> list[Line]:
        src = Source(source, self.spec.lex)
        return [Line(i, t) for i, t in sorted(src.normalized(code_only, []).items())]
