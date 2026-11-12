"""Thin wrapper over the git command line.

Every path exchanged with this module is relative to the repository root and uses POSIX separators.
git runs with cwd at the repository root and with literal pathspecs, so `*` or `?` in a folder name
is matched literally.
"""
from __future__ import annotations

import os
import subprocess
from functools import cached_property
from pathlib import Path


class GiddyError(RuntimeError):
    """User-facing error: bad comparison point, path outside the repository, git failure."""


def _run(cmd: list[str], cwd: Path, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, check=False)
    except FileNotFoundError as err:
        raise GiddyError("git executable not found") from err


class Repo:
    """git repository located from any path inside its working tree."""

    def __init__(self, path: str | os.PathLike = "."):
        start = Path(path).resolve()
        cwd = start if start.is_dir() else start.parent
        proc = _run(["git", "rev-parse", "--show-toplevel"], cwd)
        if proc.returncode:
            raise GiddyError(f"not inside a git work tree: {start}")
        self.root = Path(os.fsdecode(proc.stdout.strip())).resolve()

    # -- raw access -----------------------------------------------------------------------------
    def git(self, *args: str, check: bool = True, stdin: bytes | None = None) -> bytes:
        cmd = ["git", "--literal-pathspecs", "-c", "core.quotepath=false", *args]
        proc = _run(cmd, self.root, stdin)
        if check and proc.returncode:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise GiddyError(f"git {' '.join(args)}: {err}")
        return proc.stdout

    def text(self, *args: str) -> str:
        return self.git(*args).decode("utf-8", "replace").strip()

    def ok(self, *args: str) -> bool:
        return _run(["git", *args], self.root).returncode == 0

    # -- refs -----------------------------------------------------------------------------------
    @cached_property
    def branch(self) -> str | None:
        """Short name of the checked-out branch; None when HEAD is detached."""
        out = self.git("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return out.decode("utf-8", "replace").strip() or None

    @cached_property
    def head(self) -> str | None:
        out = self.git("rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
        return out.decode().strip() or None

    def mainline(self, ref: str = "HEAD") -> list[str]:
        """Full hashes of the first-parent chain ending at `ref`, oldest first.

        Position in this list is the commit index: 0 is the root commit.
        """
        if ref == "HEAD" and self.head is None:
            return []
        out = self.git("rev-list", "--first-parent", "--reverse", ref, "--")
        return out.decode().split()

    @cached_property
    def head_mainline(self) -> list[str]:
        return self.mainline("HEAD")

    @cached_property
    def head_index(self) -> dict[str, int]:
        return {sha: i for i, sha in enumerate(self.head_mainline)}

    def resolve_commit(self, spec: str) -> str | None:
        """Full hash for any commit-ish: hash or partial hash, branch, tag, HEAD~n."""
        out = self.git("rev-parse", "--verify", "-q", "--end-of-options", f"{spec}^{{commit}}", check=False)
        return out.decode().strip() or None

    def first_parent(self, sha: str) -> str | None:
        return self.resolve_commit(f"{sha}^1")

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", older, newer)

    @cached_property
    def empty_tree(self) -> str:
        """Object id of the empty tree in this repository's hash format (sha1 or sha256)."""
        return self.git("hash-object", "-t", "tree", "--stdin", stdin=b"").decode().strip()

    def local_branches(self) -> list[str]:
        out = self.text("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return out.splitlines()

    def commits(self, ref: str = "HEAD") -> list[dict]:
        """First-parent commits ending at `ref`, oldest first, with index, hashes, date, subject."""
        if ref == "HEAD" and self.head is None:
            return []
        out = self.git("log", "--first-parent", "--reverse", "--format=%H%x00%h%x00%cs%x00%s%x1e", ref, "--")
        rows = []
        for i, rec in enumerate(r for r in out.decode("utf-8", "replace").split("\x1e") if r.strip()):
            sha, short, date, subject = rec.strip("\n").split("\x00", 3)
            rows.append({"index": i, "sha": sha, "short": short, "date": date, "subject": subject})
        return rows

    def commit_info(self, sha: str) -> tuple[str, str, str]:
        """(short hash, commit date YYYY-MM-DD, subject)."""
        out = self.git("log", "-1", "--format=%h%x00%cs%x00%s", sha, "--").decode("utf-8", "replace")
        short, date, subject = out.strip("\n").split("\x00", 2)
        return short, date, subject

    # -- content --------------------------------------------------------------------------------
    def blob(self, spec: str) -> bytes | None:
        """Raw blob for `<tree-ish>:<path>` or `:<path>` (index stage 0); None when absent."""
        proc = _run(["git", "cat-file", "blob", spec], self.root)
        return proc.stdout if proc.returncode == 0 else None

    def blobs(self, specs: list[str]) -> list[bytes | None]:
        """Raw blobs for many `<tree-ish>:<path>` / `:<path>` specs in one `git cat-file --batch`."""
        if not specs:
            return []
        if any("\n" in s for s in specs):
            return [self.blob(s) for s in specs]
        out = self.git("cat-file", "--batch", stdin=("\n".join(specs) + "\n").encode())
        result: list[bytes | None] = []
        pos = 0
        for _ in specs:
            eol = out.index(b"\n", pos)
            header = out[pos:eol].split()
            pos = eol + 1
            if len(header) == 3 and header[1] == b"blob":
                size = int(header[2])
                result.append(out[pos:pos + size])
                pos += size + 1
            else:
                result.append(None)
                if len(header) == 3:                    # tree / commit object: skip its body
                    pos += int(header[2]) + 1
        return result

    def files(self, kind: str, sha: str | None = None) -> list[str]:
        """Every file of a snapshot: commit tree, index, or working tree (tracked + untracked, not ignored)."""
        if kind == "commit":
            out = self.git("ls-tree", "-r", "-z", "--name-only", sha or "HEAD")
        elif kind == "index":
            out = self.git("ls-files", "-z")
        else:
            out = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return [os.fsdecode(p) for p in out.split(b"\x00") if p]

    def untracked(self, pathspec: str) -> list[str]:
        out = self.git("ls-files", "-z", "--others", "--exclude-standard", "--", pathspec)
        return [os.fsdecode(p) for p in out.split(b"\x00") if p]
