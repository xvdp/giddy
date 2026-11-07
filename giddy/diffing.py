"""File and unit level comparison between two snapshots.

Pipeline per changed file:
    1. read old and new bytes; binary (NUL in the first 8 KiB) => listed, not diffed
    2. split both sides into units with the file's adapter; parse failure on either side => text adapter
    3. pair units by key; equal signature => dropped
    4. per remaining pair: line diff of normalized lines (difflib.SequenceMatcher, autojunk off,
       blank lines junk); a function yields one hunk spanning the whole function, a class or module
       unit yields hunks over its touched statements, the text fallback yields `context`-line hunks;
       line numbers come from the original files
    5. added file: complete normalized code kept for display
In code-only mode blank-line edits produce no +/- row.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .git import Repo
from .lang import Adapter, Line, TextAdapter, Unit, adapter_for
from .snapshots import Snapshot


@dataclass(frozen=True)
class Row:
    tag: str                            # " " context | "-" removed | "+" added
    line: Line                          # new-side line for " " and "+", old-side line for "-"
    old_no: int = 0                     # old line number of a " " row; 0 when the row has no old side


@dataclass
class Hunk:
    rows: list[Row]

    @property
    def old_start(self) -> int:
        for r in self.rows:
            if r.tag == "-":
                return r.line.no
            if r.old_no:
                return r.old_no
        return 0

    @property
    def old_len(self) -> int:
        return sum(r.tag == "-" or (r.tag == " " and r.old_no > 0) for r in self.rows)

    @property
    def new_start(self) -> int:
        return next((r.line.no for r in self.rows if r.tag != "-"), 0)

    @property
    def new_len(self) -> int:
        return sum(r.tag != "-" for r in self.rows)


@dataclass
class UnitChange:
    key: str
    status: str                         # "modified" | "added" | "removed"
    old: Unit | None
    new: Unit | None
    hunks: list[Hunk]

    @property
    def unit(self) -> Unit:
        unit = self.new or self.old
        assert unit is not None
        return unit

    @property
    def rows(self) -> list[Row]:
        return [r for h in self.hunks for r in h.rows]

    @property
    def added(self) -> int:
        return sum(r.tag == "+" for r in self.rows)

    @property
    def removed(self) -> int:
        return sum(r.tag == "-" for r in self.rows)

    def full_code(self) -> list[Line]:
        """Code of an added or removed unit; statement blocks separated by one blank line."""
        unit = self.unit
        if unit.blocks is None:
            return unit.lines
        out: list[Line] = []
        for a, b in unit.blocks:
            block = [l for l in unit.lines if a <= l.no <= b]
            out += ([Line(0, "")] if out and block else []) + block
        return out

    def span(self) -> tuple[int, int]:
        """Line range for display: whole unit for functions and text, changed lines for blocks."""
        unit = self.unit
        if unit.blocks is None:
            return unit.start, unit.end
        tag = "+" if self.new is not None else "-"
        nos = [r.line.no for r in self.rows if r.tag == tag] or [r.line.no for r in self.rows if r.tag != " "]
        return (min(nos), max(nos)) if nos else (unit.start, unit.end)


@dataclass
class FileChange:
    path: str
    status: str                         # git name-status letter: A M D R T ...
    old_path: str | None = None
    note: str = ""                      # "binary", "parse failed: line diff", ...
    fence: str = ""
    full_code: bool = True
    units: list[UnitChange] = field(default_factory=list)
    code: list[Line] | None = None      # complete normalized code of an added file

    @property
    def added(self) -> int:
        return sum(u.added for u in self.units)

    @property
    def removed(self) -> int:
        return sum(u.removed for u in self.units)


def changed_paths(repo: Repo, new: Snapshot, old: Snapshot, pathspec: str,
                  untracked: bool = False) -> list[tuple[str, str, str | None]]:
    """(status, path, old_path) from `git diff --name-status -M` between two snapshots."""
    args = ["diff", "--no-ext-diff", "--no-color", "--name-status", "-M", "-z"]
    if new.kind == "worktree" and old.kind == "index":
        pass
    elif new.kind == "worktree":
        args.append(old.tree(repo))
    elif new.kind == "index":
        args += ["--cached", old.tree(repo)]
    else:
        args += [old.tree(repo), new.commit]
    tokens = [os.fsdecode(t) for t in repo.git(*args, "--", pathspec).split(b"\x00") if t]
    out = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if status[0] in "RC":
            out.append((status[0], tokens[i + 2], tokens[i + 1]))
            i += 3
        else:
            out.append((status[0], tokens[i + 1], None))
            i += 2
    if untracked and new.kind == "worktree":
        known = {p for _, p, _ in out}
        out += [("A", p, None) for p in repo.untracked(pathspec) if p not in known]
    return sorted(out, key=lambda r: r[1])


def _decode(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", "replace")


def _is_binary(data: bytes | None) -> bool:
    return data is not None and b"\x00" in data[:8192]


def hunks(old: list[Line], new: list[Line], context: int, ignore_blank: bool) -> list[Hunk]:
    """Line diff with `context` lines around each change; hunks without a +/- row are dropped.

    Blank lines are junk for SequenceMatcher (never used as alignment anchors). With ignore_blank,
    an inserted blank line is shown as context and a deleted blank line is omitted, so blank-line
    edits never count as changes.
    """
    sm = SequenceMatcher(lambda t: not t.strip(), [l.text for l in old], [l.text for l in new], autojunk=False)
    out = []
    for group in sm.get_grouped_opcodes(context):
        rows: list[Row] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [Row(" ", n, o.no) for o, n in zip(old[i1:i2], new[j1:j2])]
                continue
            rows += [Row("-", l) for l in old[i1:i2] if not (ignore_blank and not l.text.strip())]
            rows += [Row(" " if ignore_blank and not l.text.strip() else "+", l) for l in new[j1:j2]]
        if any(r.tag != " " for r in rows):
            out.append(Hunk(rows))
    return out


def _restrict(hunk: Hunk, blocks: list[tuple[int, int]]) -> list[Hunk]:
    """Keep the +/- rows and the context rows of touched statement blocks; split at the gaps.

    A block is touched when it holds a + row. A pure deletion (- rows not followed by a + row)
    touches the block of the next context row, or of the previous one at the end.
    """
    rows = hunk.rows

    def block(no: int) -> tuple[int, int] | None:
        return next((b for b in blocks if b[0] <= no <= b[1]), None)

    touched = set()
    for k, r in enumerate(rows):
        if r.tag == "+":
            touched.add(block(r.line.no))
        elif r.tag == "-":
            nxt = next((x for x in rows[k + 1:] if x.tag != "-"), None)
            if nxt is None:
                nxt = next((x for x in reversed(rows[:k]) if x.tag != "-"), None)
            if nxt is not None and nxt.tag == " ":
                touched.add(block(nxt.line.no))
    touched.discard(None)
    out: list[Hunk] = []
    run: list[Row] = []
    for r in rows:
        if r.tag != " " or block(r.line.no) in touched:
            run.append(r)
        elif run:
            out.append(Hunk(run))
            run = []
    if run:
        out.append(Hunk(run))
    return [h for h in out if any(r.tag != " " for r in h.rows)]


def unit_hunks(old: Unit | None, new: Unit | None, adapter: Adapter, context: int,
               ignore_blank: bool) -> list[Hunk]:
    """Function: one hunk spanning the whole function. Class/module: whole diff restricted to the
    touched statement blocks. Text fallback: hunks with `context` lines."""
    a, b = (old.lines if old else []), (new.lines if new else [])
    if not adapter.full_code:
        return hunks(a, b, context, ignore_blank)
    whole = hunks(a, b, len(a) + len(b) + 1, ignore_blank)
    blocks = new.blocks if new is not None else None
    if blocks is None or old is None or not whole:
        return whole
    return _restrict(whole[0], blocks)


def _units(adapter: Adapter, text: str | None, code_only: bool) -> list[Unit] | None:
    return [] if text is None else adapter.units(text, code_only)


def compare_file(repo: Repo, new: Snapshot, old: Snapshot, status: str, path: str,
                 old_path: str | None, code_only: bool = True, context: int = 3) -> FileChange:
    change = FileChange(path, status, old_path)
    old_bytes = old.read(repo, old_path or path) if status != "A" else None
    new_bytes = new.read(repo, path) if status != "D" else None
    if _is_binary(old_bytes) or _is_binary(new_bytes):
        change.note = "binary"
        return change
    old_text, new_text = _decode(old_bytes), _decode(new_bytes)

    adapter = adapter_for(path)
    old_units = _units(adapter, old_text, code_only)
    new_units = _units(adapter, new_text, code_only)
    if old_units is None or new_units is None:
        change.note = "parse failed: line diff"
        adapter = TextAdapter(adapter.fence)
        old_units = _units(adapter, old_text, code_only) or []
        new_units = _units(adapter, new_text, code_only) or []
    change.fence, change.full_code = adapter.fence, adapter.full_code

    if status == "D":       # deleted file: listed with its units, no code
        change.units = [UnitChange(u.key, "removed", u, None, []) for u in old_units]
        return change

    if old_text is None and new_text is not None:
        change.code = adapter.code(new_text, code_only)

    old_map = {u.key: u for u in old_units}
    new_keys = {u.key for u in new_units}
    for u in new_units:
        o = old_map.get(u.key)
        if o is not None and o.signature == u.signature:
            continue
        hs = unit_hunks(o, u, adapter, context, code_only)
        if hs:
            change.units.append(UnitChange(u.key, "modified" if o else "added", o, u, hs))
    for o in old_units:
        if o.key not in new_keys:
            hs = unit_hunks(o, None, adapter, context, code_only)
            if hs:
                change.units.append(UnitChange(o.key, "removed", o, None, hs))
    return change
