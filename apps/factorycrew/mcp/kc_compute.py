"""Old name of the kc primitives, kept so `import kc_compute as kc` keeps working. Use `import kc`."""
from kc import *  # noqa: F401,F403
from kc import astar, connect, footprint, tile, unreached, unwired, xy  # noqa: F401
