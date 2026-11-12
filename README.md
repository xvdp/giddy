# giddy

`git diff` wrap to static `.md` with simple options and filters. Outputs static `.md` with fenced diff including extents of the functional unit. Omits docstring and formatting changes.

```diff
def func():
    all unchanged code up to def, class, trait, etc...
    ...
-    removed code
+    added code
    ...
    return
```

## Install

```bash
pip install .
giddy --help          # or: python -m giddy --help
```

Requires Python >= 3.9 and git. Python parsed by running interpreter: newer syntax falls back to a plain line diff.

## Usage

```bash
giddy   # Defaults: diff HEAD vs its parent, all files, under '.' cwd, write to:
        # git_root/.giddy/`<branch>_<commitindex>_<previndex>_<folder>_<filters>.md`
giddy -10 HEAD  # 2 entries: HEAD vs 10 commits back
        # -<int> | ~<int>   Nth ancestor
giddy S # one commit entry for (S | U) compares to HEAD
        # 'S' | staged | cached | index       'git add' staged changes

# filter by path
giddy U 0042 -p src/ops    # unstaged files vs 42nd commit, files under src/ops
        # U | unstaged | worktree | wt  unstaged files
        # 0042 | 42                     commit index
        # -p | -path                    diff files under path

# file type filters
giddy a1b2c3 -e py,rs   # commit a1b2c3 vs parent, .py and .rs files
        # SHA-1             commit by SHA-1 hash against parent
        # -e | --ext        filter by extension ( default 'all')
    giidy -e py,rs      # only python and rust
    giddy -e all        # every file

# Ouptut control
giddy 1 --stdout    # commit 1 vs commit 0
                    # --stdout  # print only
giddy -o `<path>.md`
        # -o | --out    overrides default dir [git root/.giddy/] and .md file
giddy -C 5          # show 5 context lines on non-Python files (default: 3)
        # -C | --context
giddy --open 10     # fold output on functions > 10 lines (default: 40)

# Call trees (Python)
giddy 0214 -s scripts docs   # also search scripts/ and ```python fences in docs/*.md for callers
        # -s | --scripts      extra caller folders; put after the comparison points
giddy -n                     # no call-tree section
        # -n | --no-call
```
**Outputs**

<samp>

Writes diff .md:  `<output dir>`/`<branch>_<commitindex>_<oldercommitindex>_<folder>_<filters>.md`.

Default output_dir: `<git project root>`/`.giddy/`. Can be overridden `-o <path>/<name>`

Writes commit table: `<output dir>`/`commits.md`, listed by a `(commit_index)%04d_<SHA-1 hash>`.

If no code change found, nothing is written and the nearest code change with same path and ext is listed.

A file with 2+ changed units is wrapped in an open fold whose summary lists them by tag, methods grouped by class: `ADD _posed(), RenderStack[.set_out_type() ._as_mask()] · MOD ToneMapStep.run()`.

</samp>

### Call trees

A folded **Call trees** section at the end of each report holds, per added or modified Python function or class, its incoming call hierarchy: callers, their callers, up to scripts and `.md` code fences.

```
gett.stack.RenderStack._as_image()  MOD          <- root, links to its definition
├── gett.stack.RenderStack.output()  MOD         <- bold: changed in this diff, gets no tree of its own
│   ├── analysis.parameter_sweep._rung_image() ~ <- each name links to its call site
│   │   └── analysis.parameter_sweep.sweep_stack()
│   └── gett.render.render_fitted() ~
│       └── mote_paths.stack()                   <- from -s scripts
└── gett.stack.RenderStack.reproject()  MOD
```

- Scope: `.py` files under `-p` and `-s`, plus ```` ```python ```` fences in `.md` files under `-s`, read from the new snapshot (commit, staged or working tree).
- Names: module path without its top package, `gutai.gett.stack` → `gett.stack`.
- Order: lowest call level first. A changed definition reached by an earlier tree is bold there and gets no tree.
- Resolution is static (`ast`): imports, relative imports, package re-exports, `self.`/`cls.`/`super()`, `Class.m`, `module.f`, and `Class()` as a call to `__init__` resolve exactly. A call on an untyped receiver, `x.m()`, is matched by method name only when exactly one method of that name exists in scope, marked `~`. Functions passed as values (callbacks) are not traced.
- Marks: `↑` expanded above · `↻` recursion · `…` depth limit (8).


| commit | date | subject | reports |
|---|---|---|---|
| `0207_ec5ea73` | YY-MM-DD | ... | `links to diff .md` |

**Commit entry options**
```bash
U | unstaged | worktree | wt        # unstaged files
S | staged | cached | index         # files staged with `git add`
HEAD | HEAD~<int> | <-int> | <~int> # <int>th ancestor of HEAD 
<SHA-1 hash>                        # short or long hash
<int>                               # commit index starting with 0

hash, partial hash, branch, tag, HEAD~<int> # processed with git rev-parse
```

### Languages
```
            extensions
python      py pyi          func, classes, mod stm
c           c               func, struct|union, file stm
c++ CUDA    cpp cc cxx h hh hpp hxx ipp inl tpp cu cu
                            func, op, class|struct|union|namespace|extern "C"
rust        rs              fn, impl|trait|mod|extern blocks
typescript  ts tsx mts cts  func, methods, arrow func., class|interface|namespace|mod
javascript  js jsx mjs cjs  func, methods , arrow func., class
glsl        glsl vert frag comp geom tesc tese mesh task rgen rchit rmiss ...
                            func, struct
julia       jl              funct|macro, short form f(x) = ..., struct

abbreviations: `functions`: func, `module`: mod, `statements`: stm, `operators`: op
```
```
Other ext:  line diff with `-C` context lines.
Overloads:  keyed with parameter list, e.g. `spacing(g::Grid, i)`.
Non-python:  use shared tokenizer and brace|end matcher in ./giddy/lang/ with no dependencies.
Directives  #if|#elif|#else`:  followed along the first branch, includes tokens in other branches.
```
On parsing fail, returns a line diff tagged `parse failed`.

**Adding a language:**

subclass `Adapter` in `giddy/lang/base.py`, or add a `CSpec` entry for a brace language, `giddy/lang/clike.py`, then register its extensions in `giddy/lang/__init__.py`.

## Python API

```python
from giddy import Options, compare, write

result = compare(Options(new="S", path="src", exts=("py",)))
print(result.markdown)
write(result)            # report + commits.md

from giddy import call_stack

rep = call_stack(["gutai.gett.stack.RenderStack.reference",       # dotted name
                  "gutai/src/gutai/gett/render.py:render_mitsuba",  # file:qualname
                  some_function],                                    # function or class object
                 out="calls", paths=("gutai/src",), scripts=("gutai/scripts",), snapshot="U")
rep.path       # calls/calls_gett.stack.RenderStack.reference+2.md
rep.markdown   # per function: location `path:line`, signature, call tree
```

## Tests

```bash
python -m pytest tests
```
