"""Markdown report.

Layout
    <!-- giddy {metadata json} -->      first line; read back by index.py
    title, snapshot table, scope line
    file table: anchor link per file, status, unit count, +/- line counts
    per file: anchor, link to the file in the work tree, then
        added file      one closed <details> holding the complete file (language fence)
        other files     one <details> per changed unit
            modified unit   diff fence; a function appears whole, context rows prefixed by one space
            added/removed   complete unit code (language fence)
            body <= open_max lines: <details open>, summary = unit name, status, line range, +/- counts
            body >  open_max lines: closed <details>, summary also previews the -/+ rows
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from .diffing import FileChange, UnitChange
from .lang import Line

SUMMARY_LINES = 8
OPEN_MAX = 40
STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type changed"}


def _fence(body: str, lang: str = "") -> str:
    runs = re.findall(r"`{3,}", body)
    ticks = "`" * max([3] + [len(r) + 1 for r in runs])
    return f"{ticks}{lang}\n{body}\n{ticks}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(lines: list[Line]) -> str:
    """Join lines, collapsing runs of blank lines to one and trimming leading/trailing blanks."""
    out: list[str] = []
    for l in lines:
        if l.text.strip() or (out and out[-1].strip()):
            out.append(l.text)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _patch(change: UnitChange) -> list[str]:
    parts = []
    for h in change.hunks:
        parts.append(f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@ {change.unit.label}")
        parts += [r.tag + r.line.text for r in h.rows]
    return parts


def _head(change: UnitChange) -> str:
    a, b = change.span()
    span = ("old " if change.new is None else "") + (f"L{a}" if a == b else f"L{a}–{b}")
    return f"<b>{html.escape(change.unit.label)}</b> · {change.status} · {span} · +{change.added} −{change.removed}"


def _preview(change: UnitChange) -> str:
    rows = [r.tag + r.line.text for r in change.rows if r.tag != " "]
    shown = "\n".join(rows[:SUMMARY_LINES])
    if len(rows) > SUMMARY_LINES:
        shown += f"\n… {len(rows) - SUMMARY_LINES} more"
    return f"\n<pre>{html.escape(shown)}</pre>"


def _link(root: Path, outdir: Path, path: str, line: int | None = None) -> str:
    rel = Path(os.path.relpath(root / path, outdir)).as_posix()
    return quote(rel) + (f"#L{line}" if line else "")


def _details(summary: str, body: str, is_open: bool) -> str:
    tag = "<details open>" if is_open else "<details>"
    return "\n".join([tag, f"<summary>{summary}</summary>", "", body, "", "</details>", ""])


def _unit_block(change: UnitChange, fc: FileChange, open_max: int) -> str:
    if change.status == "modified" or not fc.full_code:
        lines = _patch(change)
        body = _fence("\n".join(lines), "diff")
        preview = True
    else:
        code = _code(change.full_code())
        lines = code.split("\n")
        body = _fence(code, fc.fence)
        preview = False
    is_open = len(lines) <= open_max
    summary = _head(change) + (_preview(change) if preview and not is_open else "")
    return _details(summary, body, is_open)


def _file_block(fc: FileChange) -> str:
    code = _code(fc.code or [])
    n = code.count("\n") + 1 if code else 0
    summary = f"<b>{html.escape(fc.path)}</b> · new file · {n} lines · {len(fc.units)} units"
    return _details(summary, _fence(code, fc.fence), False)


def render(meta: dict, files: list[FileChange], skipped: list[str], root: Path, outdir: Path,
           open_max: int = OPEN_MAX) -> str:
    new, old = meta["new"], meta["old"]
    out = [f"<!-- giddy {json.dumps(meta, separators=(',', ':'))} -->",
           f"# {meta['branch']} · {new['label']} ← {old['label']}", "",
           "| side | snapshot | commit | date | subject |", "|---|---|---|---|---|"]
    for side, s in (("new", new), ("old", old)):
        name = {"index": "staged", "worktree": "unstaged", "empty": "empty tree"}.get(s["kind"], s["label"])
        commit = f"`{s['short']}`" if s.get("short") else ""
        out.append(f"| {side} | {name} | {commit} | {s.get('date', '')} | {_cell(s.get('subject', ''))} |")
    mode = "code only: docstrings, comments, blank lines and formatting excluded" if meta["code_only"] else "raw"
    exts = ", ".join(meta["exts"]) if meta["exts"] else "all"
    out += ["", f"path `{meta['path']}` · extensions `{exts}` · {mode}", "",
            f"## Files ({len(files)})", "", "| # | file | status | units | + | − |", "|---|---|---|---|---|---|"]
    for i, fc in enumerate(files, 1):
        note = f" ({fc.note})" if fc.note else ""
        out.append(f"| {i} | [{_cell(fc.path)}](#f{i}) | {fc.status}{note} | {len(fc.units)} | {fc.added} | {fc.removed} |")
    if skipped:
        out += ["", "No code change: " + ", ".join(f"`{p}`" for p in skipped)]
    out.append("")

    for i, fc in enumerate(files, 1):
        first = fc.units[0].new.start if fc.units and fc.units[0].new else None
        line = f"[{fc.path}]({_link(root, outdir, fc.path, first)}) · {STATUS.get(fc.status, fc.status)}"
        if fc.old_path and fc.status in "RC":
            line += f" from `{fc.old_path}`"
        if fc.note:
            line += f" · {fc.note}"
        out += [f'<a id="f{i}" name="f{i}"></a>', "", f"## {i}. {fc.path}", "", line, ""]
        if fc.status == "D":
            if fc.units:
                out += ["Removed units: " + ", ".join(f"`{u.unit.label}`" for u in fc.units), ""]
            continue
        if fc.code is not None:
            out.append(_file_block(fc))
        else:
            out += [_unit_block(u, fc, open_max) for u in fc.units]
    return "\n".join(out).rstrip() + "\n"
