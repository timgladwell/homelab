#!/usr/bin/env python3
"""Hydrate a built manifest with the variables Flux would substitute, in place.

The ConfigMaps are not passed in and not hand-listed. Everything needed is
already in the built output: `kustomize build clusters/<site>` emits both the
Flux `Kustomization` objects (whose postBuild.substituteFrom names the
ConfigMaps) and the ConfigMaps themselves. So this reads the same list Flux
reads, and a site that adds a third ConfigMap is picked up with no change here.

Hand-listing the paths instead would work until the day it did not, and the
failure would arrive as step 7 blaming cluster-vars for a variable that lives
somewhere else entirely.

Text-level replacement per YAML document, not a yaml round-trip: the built
output is what later steps report line numbers against, so it must come out
formatted the way kustomize wrote it.
"""
import pathlib
import sys

import yaml

built = pathlib.Path(sys.argv[1])
raw = built.read_text()
docs = raw.split("\n---\n")

DISABLED = "kustomize.toolkit.fluxcd.io/substitute: disabled"

wanted: list[str] = []          # ConfigMap names, in the order Flux applies them
available: dict[str, dict] = {}  # name -> data

for doc in docs:
    try:
        obj = yaml.safe_load(doc)
    except yaml.YAMLError:
        continue  # a doc kustomize emitted that we cannot parse is not ours to fix
    if not isinstance(obj, dict):
        continue
    kind = obj.get("kind")
    if kind == "ConfigMap" and (obj.get("metadata") or {}).get("namespace") == "flux-system":
        available[obj["metadata"]["name"]] = obj.get("data") or {}
    elif kind == "Kustomization":
        for ref in ((obj.get("spec") or {}).get("postBuild") or {}).get("substituteFrom") or []:
            if ref.get("kind") == "ConfigMap" and ref["name"] not in wanted:
                wanted.append(ref["name"])

missing = [n for n in wanted if n not in available]
if missing:
    # Flux would fail the reconcile outright rather than substitute nothing.
    print(f"substituteFrom names a ConfigMap not built into this site: {', '.join(missing)}",
          file=sys.stderr)
    sys.exit(1)

if not wanted:
    print("no postBuild.substituteFrom found — nothing would be substituted", file=sys.stderr)
    sys.exit(1)

variables: dict[str, str] = {}
for name in wanted:
    for key, value in available[name].items():
        # Later entries win in Flux, so a real override is legal. A key
        # colliding with an *identical* value is duplication worth removing,
        # but not worth failing a build over.
        variables[key] = str(value)

out = []
for doc in docs:
    if DISABLED not in doc:
        for key, value in variables.items():
            doc = doc.replace("${%s}" % key, value)
    out.append(doc)

built.write_text("\n---\n".join(out))
