"""Adapter registry keyed by lower-case file extension without the dot."""
from __future__ import annotations

from .base import Adapter, Line, Unit, split_lines
from .clike import SPECS, CLikeAdapter
from .julia import JuliaAdapter
from .python import PythonAdapter
from .text import TextAdapter

_EXTS = {
    "python": ("py", "pyi"),
    "c": ("c",),
    "cpp": ("cpp", "cc", "cxx", "c++", "h", "hh", "hpp", "hxx", "h++", "ipp", "inl", "tpp", "cu", "cuh"),
    "rust": ("rs",),
    "ts": ("ts", "tsx", "mts", "cts"),
    "js": ("js", "jsx", "mjs", "cjs"),
    "glsl": ("glsl", "vert", "frag", "comp", "geom", "tesc", "tese", "mesh", "task", "rgen", "rint",
             "rahit", "rchit", "rmiss", "rcall", "vs", "fs"),
    "slang": ("slang", "slangh", "hlsl", "hlsli", "fx", "fxh"),
    "julia": ("jl",),
}

ADAPTERS: dict[str, Adapter] = {}
for _lang, _exts in _EXTS.items():
    _adapter = PythonAdapter() if _lang == "python" else JuliaAdapter() if _lang == "julia" \
        else CLikeAdapter(SPECS[_lang])
    ADAPTERS.update({e: _adapter for e in _exts})

FENCES = {"wgsl": "wgsl", "sh": "bash", "toml": "toml", "json": "json", "yaml": "yaml", "yml": "yaml",
          "md": "markdown"}


def adapter_for(path: str) -> Adapter:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ADAPTERS.get(ext) or TextAdapter(FENCES.get(ext, ext))


__all__ = ["Adapter", "Line", "Unit", "split_lines", "adapter_for", "TextAdapter", "PythonAdapter",
           "CLikeAdapter", "JuliaAdapter", "ADAPTERS"]
