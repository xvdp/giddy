"""giddy: code-level git diffs written to markdown."""
from .version import version as __version__
from .callgraph import call_stack
from .core import Options, compare, write

__all__ = ["Options", "call_stack", "compare", "write", "__version__"]
