"""Load the repo's standalone Python programs as importable modules.

Neither program is a package. They are plain files shipped to the cluster
inside a ConfigMap (base/landing/app.py, base/pihole-sync/sync.py and the
observability.py beside it), so there is no install step — the tests load the
entry point by file path and put its directory on the import path, as the pod
does. Keeping them plain scripts is deliberate: it is what lets the pod run
them with no build, and the cost is this short loader.
"""
import importlib.util
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(relpath, env=None):
    """Import <repo>/relpath as a module, with `env` set first if given.

    sync.py reads required configuration at import time, so the environment has
    to exist before exec_module rather than inside a test.
    """
    for key, value in (env or {}).items():
        os.environ.setdefault(key, value)
    path = REPO_ROOT / relpath
    # A program shipped with sibling modules (pihole-sync's observability.py)
    # finds them beside itself in the pod; do the same here.
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
