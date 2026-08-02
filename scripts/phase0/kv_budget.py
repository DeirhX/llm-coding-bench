#!/usr/bin/env python3
"""Turn Ollama's 8 GiB paged-out snapshot budget into something you can plan against.

The budget (`maxPagedOutBytes` in `x/mlxrunner/prefix_cache.go`) is in bytes, and every attempt here
to express it in tokens produced a different answer -- 18,676 from one probe, 7,530 from another.
The reason is in `x/mlxrunner/cache/`: a snapshot is taken per layer, and the two cache types keep
different amounts.

    KVCache.Snapshot(from)        -> [from, offset), the node's whole edge
    RotatingKVCache.Snapshot(o)   -> min(o, window), the window and nothing more

So a model with mostly windowed layers pays a large *flat* cost per paged-out node and a small
per-token one, and the thing to economise is the number of parked branch points, not their length.
Tokens were never the variable those probes thought they were measuring.

Reads the geometry from the model's own config rather than taking it on trust, and touches nothing:
no request, no load, no eviction. Run it before sizing a fan-out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

MODELS = os.path.expanduser("~/.ollama/models")
BUDGET = 8 << 30                        # maxPagedOutBytes, hard-coded in the runner
WIDTH = {"bfloat16": 2, "float16": 2, "float32": 4, "int8": 1}


def config_for(model: str) -> dict:
    """The HF config.json layer of a safetensors model, found through its manifest."""
    name, _, tag = model.partition(":")
    path = os.path.join(MODELS, "manifests/registry.ollama.ai/library", name, tag or "latest")
    manifest = json.load(open(path))
    for layer in manifest["layers"]:
        if layer.get("name") == "config.json":
            digest = layer["digest"].replace(":", "-")
            return json.load(open(os.path.join(MODELS, "blobs", digest)))
    raise SystemExit("no config.json layer in %s -- a GGUF model? this reads safetensors" % model)


def report(model: str) -> None:
    cfg = config_for(model)
    text = cfg.get("text_config", cfg)
    kinds = Counter(text.get("layer_types") or ["full_attention"] * text["num_hidden_layers"])
    full = kinds.get("full_attention", 0)
    windowed = kinds.get("sliding_attention", 0)
    window = int(text.get("sliding_window") or 0)
    width = WIDTH.get(str(text.get("dtype") or cfg.get("dtype")), 2)
    per_layer_token = 2 * int(text["num_key_value_heads"]) * int(text["head_dim"]) * width

    flat = windowed * window * per_layer_token
    per_token = full * per_layer_token

    print("%s: %d layers (%d full, %d windowed at %d), %d KV heads x %d, %s"
          % (model, text["num_hidden_layers"], full, windowed, window,
             text["num_key_value_heads"], text["head_dim"], text.get("dtype")))
    print("  one layer, one token      %6.1f KiB" % (per_layer_token / 1024))
    print("  flat, per paged-out node  %6.1f MiB   (%d windowed layers x %d tokens)"
          % (flat / 1048576, windowed, window))
    print("  growth, per edge token    %6.1f KiB   (%d full-attention layers)"
          % (per_token / 1024, full))
    print("  8 GiB buys either %d parked nodes past the first %d tokens,"
          % (BUDGET // flat if flat else 0, window))
    print("                 or one node of %s tokens."
          % ("{:,}".format((BUDGET - flat) // per_token) if per_token else "unbounded"))
    for nodes in (2, 4, 8):
        room = BUDGET - nodes * flat
        if room > 0 and per_token:
            print("     %d nodes: %s tokens of edge each" % (nodes, "{:,}".format(room // nodes // per_token)))
        else:
            print("     %d nodes: over budget on windows alone" % nodes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model", nargs="?", default="gemma4-31b-mtp-96k")
    report(ap.parse_args().model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
