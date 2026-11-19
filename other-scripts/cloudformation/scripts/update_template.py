#!/usr/bin/env python3
"""Copy edited Python sources into the template's inline Lambda code."""
import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "template.json"
template = json.loads(path.read_text())
for logical, folder in (("IndexFunction", "index-photos"), ("SearchFunction", "search-photos")):
    source = (root / "lambda-functions" / folder / "index.py").read_text()
    ast.parse(source)
    template["Resources"][logical]["Properties"]["Code"]["ZipFile"] = source
path.write_text(json.dumps(template, indent=2) + "\n")
print("Updated both inline Lambda sources in template.json.")
