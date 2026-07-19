"""cdpx — Chrome DevTools Protocol primitives for dev agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cdpx")
except PackageNotFoundError:
    __version__ = "0+unknown"
