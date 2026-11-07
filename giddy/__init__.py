"""giddy: code-level git diffs written to markdown."""
from .version import version as __version__
from .core import Options, compare, write

__all__ = ["Options", "compare", "write", "__version__"]
