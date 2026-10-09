"""Codex rollout jsonl → Event. 레코드 유형별로 분기해 이벤트로 바꾼다."""
from __future__ import annotations
import json, pathlib, re
from .events import Event, clip, error_signature
from .lines import read_complete_lines
from .parse_claude import ParseResult

KNOWN_TYPES = frozenset({"session_meta", "turn_context", "response_item", "event_msg",
                         "token_usage_record", "world_state", "inter_agent_communication_metadata", "compacted"})
_INJECTED = ("<environment_context>", "# AGENTS.md instructions", "<user_instructions>", "<permissions instructions>")

def read_meta(path: pathlib.Path) -> tuple[str, str]:
    path = pathlib.Path(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > 50:
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "session_meta":
                p = d.get("payload") or {}
                return p.get("session_id") or p.get("id") or path.stem, p.get("cwd") or ""
    return path.stem, ""

_EXIT_FAIL = re.compile(r"^Process exited with code ([1-9]\d*)\s*$", re.M)
_HEADER = re.compile(r"^(Chunk ID|Wall time|Process exited with code|Original token count):?")
_OUTPUT = re.compile(r"^Output:")

def _strip_header(txt: str) -> str:
    """exec 출력 머리의 메타 줄을 위에서부터 걷어 낸다. `Output:` 줄이 머리의 끝이라 그 뒤는 본문이다."""
    lines = txt.split("\n")
    i = 0
    while i < len(lines):
        if _OUTPUT.match(lines[i]):
            i += 1
            break
        if not _HEADER.match(lines[i]):
            break
        i += 1
    return "\n".join(lines[i:])

def _output_text(out) -> str:
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        return "\n".join(b.get("text", "") for b in out if isinstance(b, dict))
    return ""

def parse(path: pathlib.Path, start: int = 0, session_id: str = "", cwd: str = "") -> ParseResult:
    path = pathlib.Path(path)
    lines, end = read_complete_lines(path, start)
    res = ParseResult(end=end)
    sid, wd = session_id or path.stem, cwd
    names: dict[str, str] = {}
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            res.malformed += 1
            continue
        if not isinstance(d, dict):
            res.malformed += 1
            continue
        res.records += 1
        t = d.get("type")
        if t not in KNOWN_TYPES:
            res.unknown += 1
            continue
        p = d.get("payload") or {}
        ts = d.get("timestamp", "")
        if t == "session_meta":
            sid = p.get("session_id") or sid
            wd = p.get("cwd") or wd
            continue
        if t != "response_item":
            continue
        pt = p.get("type")
        mk = lambda kind, text="", tool="", sig="": Event("codex", sid, ts, wd, kind, clip(text), tool, sig)
        if pt == "message" and p.get("role") == "user":
            for b in p.get("content") or []:
                txt = (b or {}).get("text", "") if isinstance(b, dict) else ""
                if txt and not txt.lstrip().startswith(_INJECTED):
                    res.events.append(mk("user_msg", txt))
        elif pt in ("custom_tool_call", "function_call"):
            name = p.get("name", "")
            names[p.get("call_id", "")] = name
            arg = p.get("input") if pt == "custom_tool_call" else p.get("arguments")
            res.events.append(mk("tool_call", arg if isinstance(arg, str) else json.dumps(arg, ensure_ascii=False), name))
        elif pt in ("custom_tool_call_output", "function_call_output"):
            txt = _output_text(p.get("output"))
            tool = names.get(p.get("call_id", ""), "")
            if txt.startswith("Script failed"):
                body = txt.split("\n", 1)[1] if "\n" in txt else txt
                sig = error_signature(body) if body.strip() else error_signature(f"{tool} script failed")
                res.events.append(mk("tool_error", body, tool, sig))
            elif txt.startswith("Secret-like value"):
                first = txt.split("\n", 1)[0]
                res.events.append(mk("permission_denied", first, tool, error_signature(first + tool)))
            elif (m := _EXIT_FAIL.search(txt)):
                body = _strip_header(txt)
                sig = error_signature(body) if body.strip() else error_signature(f"{tool} exit {m.group(1)}")
                res.events.append(mk("tool_error", body, tool, sig))
    return res
