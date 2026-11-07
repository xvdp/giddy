"""Comparison points.

A snapshot is one state of the repository's files:

=========  ===========================================  =====
kind       content                                      label
=========  ===========================================  =====
commit     tree of a commit                             %04d commit index, or 7-char hash off the mainline
index      staging area (`git add`)                     S
worktree   files on disk                                U
empty      empty tree: parent of a root commit          E
=========  ===========================================  =====

Commit index: position on the first-parent chain of HEAD, oldest first, root commit = 0.

Comparison-point grammar accepted by `resolve`:

    S | staged | stage | cached | index         -> index
    U | unstaged | worktree | wt | working      -> worktree
    -N | ~N                                     -> N first-parent steps behind HEAD
    N            (1 to 6 digits, N < #commits)   -> commit index N
    anything else accepted by `git rev-parse`   -> hash, partial hash, branch, tag, HEAD~n
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .git import GiddyError, Repo

STAGED = {"s", "staged", "stage", "cached", "index"}
UNSTAGED = {"u", "unstaged", "worktree", "wt", "working"}
_RANK = {"empty": 0, "commit": 1, "index": 2, "worktree": 3}


@dataclass(frozen=True)
class Snapshot:
    kind: str
    sha: str | None = None
    index: int | None = None

    @property
    def commit(self) -> str:
        """Hash of a commit snapshot."""
        assert self.sha is not None, f"{self.kind} snapshot has no commit"
        return self.sha

    @property
    def label(self) -> str:
        if self.kind == "index":
            return "S"
        if self.kind == "worktree":
            return "U"
        if self.kind == "empty":
            return "E"
        return f"{self.index:04d}" if self.index is not None else self.commit[:7]

    @property
    def rank(self) -> int:
        return _RANK[self.kind]

    @property
    def is_live(self) -> bool:
        """True for staging area and working tree: states that are not commits."""
        return self.kind in ("index", "worktree")

    def tree(self, repo: Repo) -> str:
        """Tree-ish argument for git diff (commit or empty tree)."""
        return repo.empty_tree if self.kind == "empty" else self.commit

    def read(self, repo: Repo, path: str) -> bytes | None:
        if self.kind == "empty":
            return None
        if self.kind == "commit":
            return repo.blob(f"{self.sha}:{path}")
        if self.kind == "index":
            return repo.blob(f":{path}")
        full = repo.root / path
        try:
            return full.read_bytes() if full.is_file() else None
        except OSError:
            return None


def commit_snapshot(repo: Repo, sha: str) -> Snapshot:
    return Snapshot("commit", sha, repo.head_index.get(sha))


def resolve(repo: Repo, spec: str) -> Snapshot:
    """Parse one comparison point; see module docstring for the grammar."""
    token = spec.strip()
    low = token.lower()
    if low in STAGED:
        return Snapshot("index")
    if low in UNSTAGED:
        return Snapshot("worktree")

    line = repo.head_mainline
    if re.fullmatch(r"[-~]\d+", token):
        if not line:
            raise GiddyError("repository has no commits")
        i = len(line) - 1 - int(token[1:])
        if i < 0:
            raise GiddyError(f"{token}: branch has only {len(line)} commits")
        return Snapshot("commit", line[i], i)
    if re.fullmatch(r"\d{1,6}", token) and int(token) < len(line):
        i = int(token)
        return Snapshot("commit", line[i], i)

    sha = repo.resolve_commit(token)
    if sha is None:
        hint = f" (commit indices run 0..{len(line) - 1})" if token.isdigit() and line else ""
        raise GiddyError(f"unknown comparison point: {spec!r}{hint}")
    return commit_snapshot(repo, sha)


def previous(repo: Repo, snap: Snapshot) -> Snapshot:
    """Default partner of a single comparison point.

    commit -> its first parent (empty tree for a root commit); index and worktree -> HEAD.
    """
    if snap.kind == "commit":
        if snap.index is not None:
            return Snapshot("commit", repo.head_mainline[snap.index - 1], snap.index - 1) if snap.index else Snapshot("empty")
        parent = repo.first_parent(snap.commit)
        return commit_snapshot(repo, parent) if parent else Snapshot("empty")
    if snap.is_live:
        return commit_snapshot(repo, repo.head) if repo.head else Snapshot("empty")
    raise GiddyError("the empty tree has no previous state")


def _newer(repo: Repo, a: Snapshot, b: Snapshot) -> bool:
    if a.rank != b.rank:
        return a.rank > b.rank
    if a.kind == "commit":
        if a.index is not None and b.index is not None:
            return a.index > b.index
        if repo.is_ancestor(a.commit, b.commit):
            return False
    return True


def resolve_pair(repo: Repo, a: str | None, b: str | None = None) -> tuple[Snapshot, Snapshot]:
    """(new, old). Order follows age: worktree > index > commits by index or ancestry > empty.

    Two unrelated commits keep the order given (first = new).
    """
    if a is None and repo.head is None:
        raise GiddyError("repository has no commits: compare S or U against the empty tree")
    first = resolve(repo, a if a is not None else "HEAD")
    second = resolve(repo, b) if b is not None else previous(repo, first)
    if first == second:
        raise GiddyError(f"both comparison points resolve to {first.label}")
    return (first, second) if _newer(repo, first, second) else (second, first)


def describe(repo: Repo, snap: Snapshot) -> dict:
    """JSON-serialisable description used in report metadata and headers."""
    info = {"kind": snap.kind, "label": snap.label, "sha": snap.sha, "index": snap.index}
    if snap.kind == "commit":
        info["short"], info["date"], info["subject"] = repo.commit_info(snap.commit)
    return info


__all__ = ["Snapshot", "resolve", "resolve_pair", "previous", "describe", "commit_snapshot"]
