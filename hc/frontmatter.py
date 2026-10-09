"""마크다운 frontmatter의 단순 key: value 해석(v1 inventory._front 규칙). YAML 전체를 해석하지 않는다."""
from __future__ import annotations
import re

_FM = re.compile(r"^---\n(.*?)\n---", re.S)

def front(text: str) -> dict:
    m = _FM.match(text or "")
    out, key = {}, None
    if not m:
        return out
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = "" if val in ("|", ">") else val.strip("\"'")
        elif key and line.startswith("  "):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out

def front_file(path, limit: int = 16384) -> dict:
    with open(path, "rb") as fh:
        return front(fh.read(limit).decode("utf-8", errors="replace"))
