"""Nearest commits with a code change under a path, reported when a comparison comes out empty.

Search runs on the first-parent chain of HEAD (`git log --first-parent --diff-merges=first-parent`),
so a merge counts by its diff against its first parent and every hit has a commit index.

Candidates are commits whose diff touches a file under the path with a selected extension, the
equivalent of `git log --oneline -- <path>` restricted to those extensions. A candidate counts as a
code change only when the same filter as the report finds a changed unit, so a commit that edits
only docstrings, comments or layout is skipped. At most `LIMIT` candidates are examined per direction.
"""
from __future__ import annotations

import os

from .diffing import compare_file, is_reported, matches_ext
from .git import Repo
from .snapshots import Snapshot, commit_snapshot, previous

LIMIT = 500


def _log(repo: Repo, revs: list[str], pathspec: str, limit: int | None = None
         ) -> list[tuple[str, list[tuple[str, str, str | None]]]]:
    """[(sha, [(status, path, old_path), ...])], newest first."""
    args = ["log", "--first-parent", "--diff-merges=first-parent", "-M", "-z", "--format=%x01%H",
            "--name-status"]
    if limit:
        args.append(f"-n{limit}")
    out = repo.git(*args, *revs, "--", pathspec)
    commits = []
    for chunk in out.split(b"\x01")[1:]:
        sha, _, rest = chunk.partition(b"\x00")
        tokens = [os.fsdecode(t) for t in rest.lstrip(b"\n").split(b"\x00") if t]
        changes, i = [], 0
        while i < len(tokens):
            status = tokens[i]
            if status[:1] in ("R", "C") and i + 2 < len(tokens):
                changes.append((status[0], tokens[i + 2], tokens[i + 1]))
                i += 3
            else:
                changes.append((status[:1], tokens[i + 1] if i + 1 < len(tokens) else "", None))
                i += 2
        commits.append((sha.decode(), changes))
    return commits


def _code_change(repo: Repo, sha: str, changes: list[tuple[str, str, str | None]],
                 exts: tuple[str, ...] | None, code_only: bool) -> bool:
    new = commit_snapshot(repo, sha)
    old = previous(repo, new)
    for status, path, old_path in changes:
        if matches_ext(path, exts) or (old_path and matches_ext(old_path, exts)):
            if is_reported(compare_file(repo, new, old, status, path, old_path, code_only)):
                return True
    return False


def _first(repo: Repo, commits, exts, code_only) -> tuple[Snapshot | None, bool]:
    """First code change in `commits`; second value True when the search stopped at LIMIT."""
    checked = 0
    for sha, changes in commits:
        if not any(matches_ext(p, exts) or (o and matches_ext(o, exts)) for _, p, o in changes):
            continue
        if _code_change(repo, sha, changes, exts, code_only):
            return commit_snapshot(repo, sha), False
        checked += 1
        if checked >= LIMIT:
            return None, True
    return None, False


def last_change(repo: Repo, start: str, pathspec: str, exts: tuple[str, ...] | None,
                code_only: bool) -> tuple[Snapshot | None, bool]:
    """Newest code change at or before commit `start`."""
    return _first(repo, _log(repo, [start], pathspec), exts, code_only)


def next_change(repo: Repo, after: str, pathspec: str, exts: tuple[str, ...] | None,
                code_only: bool) -> tuple[Snapshot | None, bool]:
    """Oldest code change after commit `after`, up to HEAD."""
    if repo.head is None or after == repo.head:
        return None, False
    return _first(repo, reversed(_log(repo, [f"{after}..HEAD"], pathspec)), exts, code_only)
