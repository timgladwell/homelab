#!/usr/bin/env python3
"""Hydrate a built manifest with a site's cluster-vars, in place.

Text-level replacement per YAML document, not a yaml round-trip: the built
output is what later steps report line numbers against, so it must come out
formatted the way kustomize wrote it.
"""
import pathlib
import sys

import yaml

built = pathlib.Path(sys.argv[1])
cluster_vars = yaml.safe_load(pathlib.Path(sys.argv[2]).read_text())["data"]

DISABLED = "kustomize.toolkit.fluxcd.io/substitute: disabled"

docs = []
for doc in built.read_text().split("\n---\n"):
    if DISABLED not in doc:
        for key, value in cluster_vars.items():
            doc = doc.replace("${%s}" % key, str(value))
    docs.append(doc)

built.write_text("\n---\n".join(docs))
