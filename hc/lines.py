"""오프셋부터 개행으로 끝난 줄만 읽는다. 쓰는 중인 마지막 줄은 남겨 둔다."""
from __future__ import annotations
import pathlib

def read_complete_lines(path: pathlib.Path, start: int) -> tuple[list[str], int]:
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read()
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], start
    body = data[: cut + 1]
    lines = [ln.decode("utf-8", errors="replace").strip() for ln in body.split(b"\n")]
    return [ln for ln in lines if ln], start + cut + 1
