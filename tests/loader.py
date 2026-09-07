"""Load the repo's standalone Python programs as importable modules.

Neither program is a package. Both are single files shipped to the cluster
inside a ConfigMap (base/landing/app.py, base/pihole-sync/sync.py), so there is
no install step and no import path to add — the tests load them by file path
instead. Keeping them plain scripts is deliberate: it is what lets the pod run
them with no build, and the cost is this eight-line loader.
"""
import importlib.util
import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(relpath, env=None):
    """Import <repo>/relpath as a module, with `env` set first if given.

    sync.py reads required configuration at import time, so the environment has
    to exist before exec_module rather than inside a test.
    """
    for key, value in (env or {}).items():
        os.environ.setdefault(key, value)
    path = REPO_ROOT / relpath
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
