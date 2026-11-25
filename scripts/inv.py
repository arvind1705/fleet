#!/usr/bin/env python3
"""Query inventory.json from shell scripts.

  inv.py node                 node name of this machine (FLEET_NODE overrides)
  inv.py nodes                all node names
  inv.py get <dotted.path>    a scalar, or a list one item per line
  inv.py stacks [node]        Compose stacks that run on a node
  inv.py serve-ports [node]   loopback service ports the node must expose on the tailnet
"""
import json
import os
import platform
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
inv = json.load(open(os.path.join(ROOT, "inventory.json")))


def this_node():
    if os.environ.get("FLEET_NODE"):
        return os.environ["FLEET_NODE"]
    want = "macos" if platform.system() == "Darwin" else "linux"
    hits = [n for n, v in inv["nodes"].items() if v.get("os") == want]
    if len(hits) != 1:
        sys.exit("cannot determine node; set FLEET_NODE")
    return hits[0]


def emit(v):
    if isinstance(v, list):
        for x in v:
            print(x)
    elif isinstance(v, (dict,)):
        print(json.dumps(v))
    elif v is not None:
        print(v)


cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "node"), sys.argv[2:]
node = args[0] if args else None
if cmd == "node":
    print(this_node())
elif cmd == "nodes":
    emit(list(inv["nodes"]))
elif cmd == "get":
    v = inv
    for part in args[0].split("."):
        v = v[part]
    emit(v)
elif cmd == "stacks":
    emit(inv["nodes"][node or this_node()].get("stacks", []))
elif cmd == "serve-ports":
    n = node or this_node()
    ports = [inv["nodes"][n]["agent_port"]]
    for s in inv["services"]:
        host, port = s["upstream"].rsplit(":", 1)
        if s["node"] == n and host in ("127.0.0.1", "localhost") and not s.get("tailnet_port_open"):
            ports.append(int(port))
    emit(sorted(set(ports)))
else:
    sys.exit(__doc__)
