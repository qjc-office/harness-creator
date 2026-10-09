"""Claude Code 전사 jsonl → Event. 레코드를 순회하며 유형별로 분기해 이벤트로 바꾼다."""
from __future__ import annotations
import json, pathlib
from dataclasses import dataclass, field
from .events import Event, clip, error_signature
from .lines import read_complete_lines

KNOWN_TYPES = frozenset({
    "user", "assistant", "system", "attachment", "queue-operation", "last-prompt", "atis-latch",
    "mode", "permission-mode", "ai-title", "pr-link", "custom-title", "file-history-snapshot",
    "file-history-delta", "cost-state", "frame-link", "relocated", "worktree-state",
    "artifact-autoreact-ledger", "artifact-comment-monitor", "agent-setting", "summary",
})

@dataclass
class ParseResult:
    events: list = field(default_factory=list)
    end: int = 0
    records: int = 0
    unknown: int = 0
    malformed: int = 0   # 개행으로 끝났지만 JSON 객체가 아닌 줄. 다시 읽어도 고쳐지지 않으므로 커서는 넘긴다

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""

def _brief_input(name: str, inp: dict) -> str:
    if name == "Bash":
        return str(inp.get("command", ""))
    for key in ("file_path", "path", "skill", "subagent_type", "pattern"):
        if inp.get(key):
            return str(inp[key])
    return ""

def parse(path: pathlib.Path, start: int = 0) -> ParseResult:
    path = pathlib.Path(path)
    lines, end = read_complete_lines(path, start)
    res = ParseResult(end=end)
    tool_names: dict[str, str] = {}
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
        sid = d.get("sessionId") or path.stem
        ts, cwd = d.get("timestamp", ""), d.get("cwd", "")
        mk = lambda kind, text="", tool="", sig="": Event("claude", sid, ts, cwd, kind, clip(text), tool, sig)
        for h in d.get("hookErrors") or []:
            res.events.append(mk("hook_error", str(h), sig=error_signature(str(h))))
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name = b.get("name", "")
                    tool_names[b.get("id", "")] = name
                    res.events.append(mk("tool_call", _brief_input(name, b.get("input") or {}), name))
        elif t == "user":
            if d.get("toolDenialKind"):
                kind_txt = str(d["toolDenialKind"])
                tool = ""
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tool = tool_names.get(b.get("tool_use_id", ""), "")
                res.events.append(mk("permission_denied", kind_txt, tool, error_signature(kind_txt + tool)))
                continue
            if isinstance(content, str):
                if not d.get("isMeta") and not d.get("isSidechain"):
                    res.events.append(mk("user_msg", content))
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        txt = _text_of(b.get("content"))
                        res.events.append(mk("tool_error", txt, tool_names.get(b.get("tool_use_id", ""), ""), error_signature(txt)))
    return res
