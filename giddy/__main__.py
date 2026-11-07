"""giddy: write the code-level diff between two repository states to markdown.

comparison points (0, 1 or 2; order does not matter, the older side is detected):
  S | staged                staging area
  U | unstaged              working tree (files on disk)
  -N                        N commits behind HEAD on the first-parent chain
  N                         commit index N (0 = root commit of the branch)
  hash | partial hash | branch | tag | HEAD~n

  none          HEAD against its parent
  one commit    against its parent
  S or U        against HEAD

examples:
  giddy                     HEAD vs HEAD~1
  giddy -10 HEAD            HEAD vs 10 commits back
  giddy S                   staged vs HEAD
  giddy U 0042 -p src/ops   working tree vs commit 42, files under src/ops
  giddy a1b2c3d -e py,rs    commit a1b2c3d vs its parent, .py and .rs files

reports go to <repo root>/.giddy/ (or $GIDDY_DIR, or --out); commits.md there indexes them.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .core import Options, compare, write
from .git import GiddyError


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="giddy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("points", nargs="*", metavar="POINT", help="comparison points, at most 2")
    ap.add_argument("-p", "--path", default=".", help="folder or file filter (default: current directory)")
    ap.add_argument("-e", "--ext", default="py", help="extensions, comma separated, or 'all' (default: py)")
    ap.add_argument("--raw", action="store_true", help="keep docstrings, comments and formatting-only changes")
    ap.add_argument("-C", "--context", type=int, default=3, help="patch context lines (default: 3)")
    ap.add_argument("--open", type=int, default=40, metavar="N",
                    help="folds with at most N lines render open, larger ones closed (default: 40; 0: all closed)")
    ap.add_argument("--untracked", action="store_true", help="with U: include untracked files as added")
    ap.add_argument("-o", "--out", default=None, help="output directory, or a .md file name")
    ap.add_argument("--stdout", action="store_true", help="print the markdown instead of writing files")
    ap.add_argument("-V", "--version", action="version", version=f"giddy {__version__}")
    args = ap.parse_args(argv)
    if len(args.points) > 2:
        ap.error("at most two comparison points")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    exts = None if args.ext.strip().lower() in ("all", "*", "") else \
        tuple(e.strip() for e in args.ext.split(",") if e.strip())
    points = args.points + [None] * (2 - len(args.points))
    opts = Options(new=points[0], old=points[1], path=args.path, exts=exts, code_only=not args.raw,
                   context=max(0, args.context), untracked=args.untracked, open_max=args.open, out=args.out)
    try:
        result = compare(opts)
        if args.stdout:
            sys.stdout.write(result.markdown)
            return 0
        n = len(result.files)
        new, old = result.meta["new"]["label"], result.meta["old"]["label"]
        if not n:
            print(f"giddy: no code changes {new} <- {old} under '{result.meta['path']}'")
            return 0
        write(result)
        print(f"giddy: {new} <- {old}: {n} file(s) -> {result.report}")
        return 0
    except GiddyError as err:
        print(f"giddy: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
