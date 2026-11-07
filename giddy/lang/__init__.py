"""Adapter registry keyed by lower-case file extension without the dot."""
from __future__ import annotations

from .base import Adapter, Line, Unit, split_lines
from .python import PythonAdapter
from .text import TextAdapter

ADAPTERS: dict[str, Adapter] = {"py": PythonAdapter(), "pyi": PythonAdapter()}

FENCES = {
    "rs": "rust", "ts": "ts", "tsx": "tsx", "js": "js", "jsx": "jsx", "mjs": "js",
    "glsl": "glsl", "vert": "glsl", "frag": "glsl", "comp": "glsl", "geom": "glsl",
    "slang": "hlsl", "hlsl": "hlsl", "wgsl": "wgsl", "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp",
    "cu": "cuda", "cuh": "cuda", "sh": "bash", "toml": "toml", "json": "json", "yaml": "yaml",
    "yml": "yaml", "md": "markdown",
}


def adapter_for(path: str) -> Adapter:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ADAPTERS.get(ext) or TextAdapter(FENCES.get(ext, ext))


__all__ = ["Adapter", "Line", "Unit", "split_lines", "adapter_for", "TextAdapter", "PythonAdapter"]
