"""Shared pytest setup: lets the tests import pihole-sync's modules by name.

pihole-sync ships as plain files in a ConfigMap, mounted in one directory, and
sync.py imports its siblings by name (`from logs import log`). Python puts a
script's own directory on its import path, which is how that works in the pod.
Here pytest is the script, so this puts the same directory on the path.

PIHOLE_URL is read when pihole_api is first imported and has no default, so it
is set before any test file does that.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "base" / "pihole-sync"))
os.environ.setdefault("PIHOLE_URL", "http://pihole.invalid")
