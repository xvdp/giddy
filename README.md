# giddy

`giddy` options is a wrap of `git diff` with simplified options and filters which writes to a fenced `.md`.

Each changed function is written in full with patched lines. Only code gets written, omits docstring and formatting changes.
```diff
def func():
    unchanged code
-    removed code
+    added code
    return
```

## Install

```bash
pip install -e .
giddy --help          # or: python -m giddy --help
```

Requires Python ≥ 3.9 and git, no other dependencies. Python sources are parsed by the running interpreter: syntax newer than the interpreter (for example PEP 701 f-strings under Python < 3.12) falls back to a plain line diff for that file.

## Usage

```bash
giddy                      # diff HEAD vs 1 commit back 
giddy -10 HEAD             # HEAD vs 10 commits back
giddy S                    # staged files vs HEAD
giddy U 0042 -p src/ops    # working tree vs commit 42, files under src/ops
giddy a1b2c3 -e py,rs      # commit a1b2c3 vs its parent, .py and .rs files
giddy 1 --stdout           # print the report instead of writing it
```


A `giddy` call writes a `<branch>_<commitindex>_<oldercommitindex>_<folder>_<filters>.md`
to output_dir, default `<git project root>/.giddy/`. File name and location can be overridden.

A commit table, `<output dir>/commits.md` lists commits  by a `%04d` commit index and SHA-1 hash, includding  and `giddy` outputs.

| commit | date | subject | reports |
|---|---|---|---|
| `0207_ec5ea73` | YY-MM-DD | … | links |
Commit table lists commits by `commit index_sha hash`.

### Comparison points

`giddy` takes 0, 1 or 2 order independent comparison points.

* *`%04d` commit index*:    `giddy` lists commits `git log --oneline` and assigns a commmit index to each.
* *`-N`|`~N`*:  Nth ancestror of HEAD.s
* *`S`|`staged`|`cached`|`index`*:    staged changes from `git add`. Equivalent to `git diff --cached`.
* *`U`|`unstaged`|`worktree`|`wt`*:    unstaged changes or working directory.
* *hash, partial hash, branch, tag, `HEAD~n`* are processed with git rev-parse.

Comparison defaults
```bash
giddy                       # HEAD to parent
giddy `<comparison point>`  # comparison point to parent
giddy `<S|U>`               # staged | working to HEAD
```

### Options
```bash
# filter by path       (-p | --path)
giddy -p `<path>`       # only git diff under path
cd  `<path>` && giddy   # idem
# filter by extension  (-e | --ext)  # default only python files
giidy -e py,rs          # only python and rust
giddy -e all            # every file
# context lines        (-C | --context N)
giddy -C `N`            # context lines fallback by non-Python files (default: 3)
# fold lines
giddy --open `N`        # fold when more than N lines
# output               (-o --out <path>) overwrites default dir [git root/.giddy]
giddy -o `<path>.md`
giddy --stdout          # print only. no file out
```
### Languages

| language | extensions | units | qualified names |
|---|---|---|---|
| Python | `py pyi` | functions, classes, module statements | `MyClass.method` |
| C | `c` | functions, `struct`/`union`, file statements | `func` |
| C++, CUDA | `cpp cc cxx h hh hpp hxx ipp inl tpp cu cuh` | functions, operators, `class`/`struct`/`union`/`namespace`/`extern "C"` | `ns::MyClass::method` |
| Rust | `rs` | `fn`, `impl`/`trait`/`mod`/`extern` blocks | `Foo::new`, `<Foo as Display>::fmt` |
| TypeScript | `ts tsx mts cts` | functions, methods, arrow functions assigned to names, `class`/`interface`/`namespace`/`module` | `MyClass.method` |
| JavaScript | `js jsx mjs cjs` | functions, methods, arrow functions assigned to names, `class` | `MyClass.method` |
| GLSL | `glsl vert frag comp geom tesc tese mesh task rgen rchit rmiss ...` | functions, `struct` | `func` |
| Slang, HLSL | `slang slangh hlsl hlsli fx fxh` | functions, `struct`/`class`/`interface`/`extension`/`namespace` | `Struct.method` |
| Julia | `jl` | `function`/`macro`, short form `f(x) = ...`, `struct`/`abstract type`/`primitive type`, `module` | `Module.func` |

Any other extension gets a plain line diff with `-C` context lines.

A function appears whole, nested closures and lambdas included. Code-only mode compares token sequences: a unit changed only in comments, doc comments, Julia docstrings or layout is dropped. A name defined more than once (C++/Slang overloads, Julia methods) is keyed with its parameter list, e.g. `spacing(g::Grid, i)`.

Non-Python languages use one shared tokenizer and brace/`end` matcher in `giddy/lang/` with no dependencies. C-family preprocessor `#if`/`#elif`/`#else` branches are followed along the first possible branch for structure; tokens in the other branches still count as changes. A file whose brackets or blocks do not balance falls back to a line diff, marked `parse failed` in the report.

Parse rate on files at HEAD:

| corpus | language | files parsed |
|---|---|---|
| Meromorph/ceen | Rust, TS, JS | 231/231 |
| sdfx | Slang | 9/9 |
| stochasticsplats | GLSL | 29/29 |
| libjpeg-turbo | C | 240/240 |
| faiss | C++, CUDA | 788/788 |
| pbrt-v4 | C++ | 213/213 |
| Oceananigans.jl | Julia | 667/668 (the one failure is invalid Julia) |

Adding a language: subclass `Adapter` (`giddy/lang/base.py`), or add a `CSpec` entry for a brace language (`giddy/lang/clike.py`), then register its extensions in `giddy/lang/__init__.py`.

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
