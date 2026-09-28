import importlib.metadata
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
packages = {}
for d in importlib.metadata.distributions():
    packages.setdefault(d.metadata["Name"].lower(), d.version)
(
    root
    / (
        "config/tts-runtime-packages.lock.json"
        if "--tts" in sys.argv
        else "config/runtime-packages.lock.json"
    )
).write_text(
    json.dumps(
        {"python": sys.version.split()[0], "packages": dict(sorted(packages.items()))},
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
