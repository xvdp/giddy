# giddy

`git diff` wrap to static `.md` with simple options and filters. Outputs static `.md` with fenced diff including extents of the functional unit. Omits docstring and formatting changes.



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

# Call trees (only implemented for python)
giddy 0214 -s scripts docs   # trace calls from dirs external to module
        # -s | --scripts        nargs+
giddy -n                     # omit call-tree section
        # -n | --no-call
```


**Commit entry options**
```bash
U | unstaged | worktree | wt        # unstaged files
S | staged | cached | index         # files staged with `git add`
HEAD | HEAD~<int> | <-int> | <~int> # <int>th ancestor of HEAD 
<SHA-1 hash>                        # short or long hash
<int>                               # commit index starting with 0

hash, partial hash, branch, tag, HEAD~<int> # processed with git rev-parse
```

## Outputs

Default output_dir: `<git project root>/.giddy/`. Can be overridden `-o <path>/<name>`

<samp>
Writes 2 documents per call. (1. commit table update, 2. filtered git diff with  call trees.)


### 1. commit table

`<output dir>/commits.md`, a commit table listed by a `(commit_index)%04d_<SHA-1 hash>`. Serves as index to (2)

| commit | date | subject | reports |
|---|---|---|---|
| `0207_ec5ea73` | YY-MM-DD | ... | `links to diff .md` |


### 2. filtered git diff

`<output dir>/<branch>_<commitindex>_<oldercommitindex>_<folder>_<filters>.md`.

Changes to code (no docstrings or lints) including the function of class containing the change Folds by function and file. Tags changed files and functions with `ADD`, `MOD`, `REMOVE`.

</samp>

<details open>
<summary>Foldable list of <b>func()</b> · modified · lines · +N −M</summary>

```diff

def func():
    all unchanged code up to def, class, trait, etc...
    ...
-    removed code
+    added code
    ...
    return
```

</details>


### 2.1 call trees

Foldable all trees for each function changed by the commit.

<details open>
<summary><b>foo.plenoptic_goo.scoobie()</b> <i>ADD</i></summary>

<pre>
<b>foo.plenoptic_goo.scoobie()</a></b> <i>ADD</i>           # commited changes
├── <b>foo.plenoptic_goo.doobie()</a></b> <i>MOD</i>
│   └── <b>foo.plenoptic_goo.do()</a></b> <i>ADD</i>
├── <b>aloo.gobi.patata()</a></b> <i>MOD</i>
│   ├── hula.baloo.Baloo.treatments()</a>
│   │   └── ... <i>scripts/ado.about_nothing()</i></a> ~  # -s external italics
</pre>
</details>



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

mod: `module`: stm: `statements`,op: `operators`.
```
```
Other ext:  line diff with `-C` context lines.
Overloads:  keyed with parameter list, e.g. `spacing(g::Grid, i)`.
Non-python: share tokenizer and brace|end matcher in ./giddy/lang/; no dependencies.
Directives  #if | #elif | #else`:  followed along the first branch, include tokens in other.
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

rep = call_stack(["foo.plenoptic_goo.scoobie",                  # dotted name
                  "mojool/src/mojoolmojool/phile.py:funkion",   # file:qualname
                  som_funktion],                                # function or class object
                 out="calls", paths=("mojool/src",), scripts=("mojool/scripts",), snapshot="U")
rep.path       # calls/call_foo.plenoptic_goo.scoobie.reference+2.md
rep.markdown   # per function: location `path:line`, signature, call tree
```

## Tests

```bash
python -m pytest tests
```
