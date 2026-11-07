# giddy

Writes the code-level diff between two states of a git repository to a markdown report. A changed Python function appears whole inside one `diff` fence, with its changed lines marked `-` and `+`. Docstring, comment and formatting changes are excluded by default.

Design notes: [designprompt.md](designprompt.md).

## Install

```bash
pip install -e .
giddy --help          # or: python -m giddy --help
```

Requires Python ≥ 3.9 and git. Python sources are parsed by the running interpreter: syntax newer than the interpreter (for example PEP 701 f-strings under Python < 3.12) falls back to a plain line diff for that file.

## Usage

```bash
giddy                      # HEAD vs its parent
giddy -10 HEAD             # HEAD vs 10 commits back
giddy S                    # staged vs HEAD
giddy U 0042 -p src/ops    # working tree vs commit 42, files under src/ops
giddy a1b2c3 -e py,rs      # commit a1b2c3 vs its parent, .py and .rs files
giddy 1 --stdout           # print the report instead of writing it
```

### Comparison points

`giddy` takes 0, 1 or 2 comparison points. Their order does not matter: giddy places the older one on the old side (working tree > staging area > commits by index or ancestry > empty tree).

| point | meaning |
|---|---|
| `S`, `staged`, `cached`, `index` | staging area (`git add`) |
| `U`, `unstaged`, `worktree`, `wt` | working tree: files on disk, staged changes included |
| `-N`, `~N` | N commits behind HEAD on the first-parent chain |
| `N` (1 to 6 digits, smaller than the commit count) | commit index N |
| anything else | resolved by `git rev-parse`: hash, partial hash, branch, tag, `HEAD~n` |

Defaults for a missing point:

| given | compared against |
|---|---|
| nothing | HEAD vs its parent |
| one commit | its first parent; the empty tree for the root commit |
| `S` or `U` | HEAD |

Commit index: position on the first-parent chain of HEAD, oldest first. The root commit has index `0000`. The first-parent chain follows the first parent at each merge, so it lists the branch's own commits.

### Options

| option | effect |
|---|---|
| `-p, --path PATH` | only files under PATH, recursively; PATH may also be a single file (default: current directory) |
| `-e, --ext py,rs` | extension filter, comma separated; `all` disables it (default: `py`) |
| `--raw` | keep docstrings, comments, and docstring-only or formatting-only changes |
| `-C, --context N` | context lines for the line-diff fallback used by non-Python files (default: 3) |
| `--open N` | folds with at most N lines render open, larger folds render closed (default: 40; `0` closes all) |
| `--untracked` | with `U`: include untracked files as added |
| `-o, --out PATH` | output directory, or a `.md` file name; absolute or relative to the current directory |
| `--stdout` | print the markdown, write nothing |

Output directory, in order of precedence: `--out`, the environment variable `GIDDY_DIR`, `<repo root>/.giddy/`.

## Output

### Report file name

```
[_]<branch>_<new>_<old>[_<folder>]_<filters>.md
```

| part | content |
|---|---|
| leading `_` | the new side is the staging area or the working tree |
| `<branch>` | current branch, `/` replaced by `-`; `HEAD` when detached |
| `<new>`, `<old>` | `%04d` commit index, `S` staged, `U` unstaged, `E` empty tree, 7-character hash for a commit off the first-parent chain |
| `<folder>` | path filter relative to the repo root, `sub/sub/folder` → `sub-sub-folder`; omitted at the root |
| `<filters>` | extensions joined by `-`, or `all`; `+raw` appended in raw mode |

Examples: `main_0207_0206_gutai-src-gutai_py.md`, `_main_S_0012_py.md`.

A comparison with no code changes writes no file.

### Report content

1. First line: an HTML comment with JSON metadata (snapshots, hashes, path, filters). `commits.md` is built from it.
2. Header: branch, snapshot table (label, short hash, date, subject), path and filter line.
3. File table: link to each file's section, git status, changed unit count, `+`/`−` line counts. Files with only non-code changes are listed below the table.
4. One section per file, with a link to the file in the working tree:
   - **added file**: one closed fold holding the complete file.
   - **deleted file**: list of the removed units.
   - **other files**: one fold per changed unit.

Units (Python):

| unit | content |
|---|---|
| function | outermost `def` / `async def`, reached through classes; closures stay inside their function; key `Klass.method` |
| class | class header plus non-function body statements |
| module | module-level statements that are not functions or classes |

Rendering of a unit:

- **modified function**: one `diff` fence holding the whole function. Context lines start with one space, so code columns stay aligned with the `-` and `+` lines.
- **modified class or module unit**: `diff` fence restricted to the statements that changed.
- **added or removed unit**: complete code in a language fence.
- **fold summary**: unit name, status, line range, `+`/`−` counts. A fold body of at most `--open` lines renders as `<details open>`: one visible section holding name and fence. A larger body renders closed, and its summary previews the first 8 `-`/`+` lines.

Repeated names (property getter and setter, conditional redefinitions) get `#2`, `#3` suffixes.

### Code-only filter (default)

- A unit counts as changed only when its Python syntax tree changed, ignoring docstrings and line positions. Comments, blank lines and layout never reach the syntax tree, so units changed only in those are dropped.
- Displayed code has docstrings and comments removed. Blank-line edits never appear as `-` or `+`.

Non-Python files: whole-file line diff with `-C` context lines; blank-line-only edits are ignored. Language adapters for Rust, TypeScript, GLSL and Slang plug into `giddy/lang/` (interface in `giddy/lang/base.py`).

### commits.md

`<output dir>/commits.md` is rebuilt on every write. It holds one table per branch that has reports, newest commit first:

| commit | date | subject | reports |
|---|---|---|---|
| `0207_ec5ea73` | 2026-11-07 | … | links |

A commit-vs-commit report links on the row of its new commit. An `S`/`U` report links on the row of the commit it compares against. Reports match rows by commit hash, so they stay attached after a rebase renumbers the branch. Reports matching no row are listed under "Other reports".

## Python API

```python
from giddy import Options, compare, write

result = compare(Options(new="S", path="src", exts=("py",)))
print(result.markdown)
write(result)            # report + commits.md
```

## Tests

```bash
python -m pytest tests
```
