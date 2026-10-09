"""세션 파일을 찾아 커서 이후만 파싱·가림해 이벤트로 적재한다."""
from __future__ import annotations
import json, os, pathlib, time
from dataclasses import dataclass, field, asdict
from .config import load_config, load_defaults, data_dir, expand, now as _now
from .events import dumps
from .fsutil import write_private
from .redact import RedactError, from_config
from . import parse_claude, parse_codex

_BASE_CFG = load_defaults()

@dataclass
class CollectStats:
    files: int = 0
    events: dict = field(default_factory=lambda: {"claude": 0, "codex": 0})
    records: int = 0
    unknown: int = 0
    deferred: int = 0
    errors: int = 0
    malformed: int = 0                 # 건너뛴 깨진 레코드 줄 수(형식 오류, 재시도 불가). > 0이면 그 주 실행은 불완전
    lexicon: bool = True
    lexicon_terms: int | None = None   # 고객명 사전 건수. 가림기가 알려 주지 않으면 None
    skipped_outside_source: int = 0    # 소스 루트 밖을 가리키는 심링크라 읽지 않은 세션 파일 수
    codex_included: bool = False       # 이번 수집에 Codex 소스를 넣었는가(첫 실행 동의 결과)

def _inside(p: pathlib.Path, root: pathlib.Path) -> bool:
    rp = p.resolve()
    return rp == root or rp.is_relative_to(root)

def _discover(sources: dict, since: float, counter: dict | None = None) -> list[tuple[str, pathlib.Path, os.stat_result]]:
    """창 안의 세션 파일. 실제 위치(resolve)가 그 소스 루트 밖인 심링크(파일·폴더)는 읽지 않고
    counter["outside"]에 센다. 끊긴 링크·그 사이 사라진 파일은 조용히 건너뛴다."""
    found = []
    cc = expand(sources["claude"]) if sources.get("claude") else None
    if cc is not None and cc.is_dir():
        for p in list(cc.glob("*/*.jsonl")) + list(cc.glob("*/*/subagents/*.jsonl")):
            found.append(("claude", p, cc.resolve()))
    cx = expand(sources["codex"]) if sources.get("codex") else None
    if cx is not None and cx.is_dir():
        for p in cx.rglob("rollout-*.jsonl"):
            found.append(("codex", p, cx.resolve()))
    fresh = []
    for s, p, root in found:
        if not _inside(p, root):
            if counter is not None:
                counter["outside"] = counter.get("outside", 0) + 1
            continue
        try:
            st = p.stat()          # 파일마다 한 번만 stat, 그 사이 사라진 파일·끊긴 링크는 건너뛴다
        except FileNotFoundError:
            continue
        if st.st_mtime >= since:
            fresh.append((s, p, st))
    return sorted(fresh, key=lambda spt: spt[2].st_mtime)

def _write_atomic(path: pathlib.Path, data: str) -> None:
    write_private(path, data)   # 0600 + 원자 교체(hc/fsutil.py)

def _prune_events(ev_dir: pathlib.Path, keep_days: int, now: float) -> None:
    """보존 기간(config events_retention_days)보다 오래된 이벤트 파일을 지운다. 파일 mtime 기준."""
    if not ev_dir.is_dir():
        return
    for f in ev_dir.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < now - keep_days * 86400:
                f.unlink()
        except FileNotFoundError:
            continue

def collect(days: int, now: float | None = None, sources: dict | None = None, redactor=None,
            include_codex: bool | None = None) -> CollectStats:
    cfg = load_config()
    red = redactor or from_config(cfg)    # 실패하면 여기서 RedactError, 아무것도 쓰지 않는다
    now = now or _now()
    if include_codex is None:
        include_codex = (cfg.get("sources") or {}).get("codex")
    include_codex = include_codex is True    # 동의는 정확히 true일 때만("false"·"0"·1은 미동의)
    paths = dict(sources or cfg["source_paths"])
    if not include_codex:
        paths.pop("codex", None)
    root = data_dir()
    _prune_events(root / "events", cfg["events_retention_days"], now)
    cur_path = root / "cursor.json"
    cursor = json.loads(cur_path.read_text()) if cur_path.exists() else {}
    skipped = {"outside": 0}
    files = _discover(paths, now - days * 86400, counter=skipped)
    # 창(window) 밖으로 나간 파일의 커서는 버린다 — 커서가 끝없이 커지지 않게
    in_window = {str(p) for _, p, _ in files}
    cursor = {k: v for k, v in cursor.items() if k in in_window}
    todo = []
    for surface, p, st in files:
        c = cursor.get(str(p))
        if c and c["size"] == st.st_size and c["mtime"] == st.st_mtime:
            continue
        todo.append((surface, p, st))
    limit = cfg["max_files_per_run"]
    flush_every = cfg["cursor_flush_every"]
    stats = CollectStats(deferred=max(0, len(todo) - limit))
    stats.skipped_outside_source = skipped["outside"]
    stats.codex_included = include_codex
    stats.lexicon = getattr(red, "has_lexicon", True)
    stats.lexicon_terms = getattr(red, "lexicon_terms", None)
    ev_dir = root / "events"
    ev_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ev_dir, 0o700)
    out_path = ev_dir / (time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)   # os.open의 mode는 새로 만들 때만 적용된다. 이미 있던 파일도 조인다
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        def save_cursor():
            # 이벤트가 디스크에 닿은 뒤에만 커서를 전진시킨다(강제 종료 시 유실 대신 중복)
            out.flush()
            os.fsync(out.fileno())
            _write_atomic(cur_path, json.dumps(cursor))
        processed = 0
        try:
            for surface, p, st in todo[:limit]:
                c = cursor.get(str(p)) or {}
                start = c.get("offset", 0) if st.st_size >= c.get("offset", 0) else 0
                try:
                    if surface == "claude":
                        res = parse_claude.parse(p, start)
                        sid, wd = "", ""
                    else:
                        sid, wd = (c.get("session_id"), c.get("cwd")) if start and c.get("session_id") else parse_codex.read_meta(p)
                        res = parse_codex.parse(p, start, sid, wd)
                    # cwd·tool 도 가린다: 고객 폴더 경로(<고객>-ax 등)와 MCP 도구 이름에 실명·토큰이 들어갈 수 있다
                    lines = [dumps(type(e)(**{**asdict(e), "text": red.redact(e.text), "cwd": red.redact(e.cwd),
                                              "tool": red.redact(e.tool)})) + "\n"
                             for e in res.events]
                except RedactError:
                    raise              # 가림 실패는 fail-closed: 이 파일은 기록하지 않고 실행을 멈춘다
                except FileNotFoundError:
                    cursor.pop(str(p), None)   # 발견 뒤 사라진 파일은 오류가 아니라 건너뛰기
                    continue
                except Exception:
                    # 깨진 레코드 하나가 주간 실행 전체를 죽이지 않게: 이 파일만 건너뛰고 커서는 그대로 둔다
                    stats.errors += 1
                    continue
                out.writelines(lines)
                stats.events[surface] += len(lines)
                stats.files += 1
                stats.records += res.records
                stats.unknown += res.unknown
                stats.malformed += res.malformed
                cursor[str(p)] = {"size": st.st_size, "mtime": st.st_mtime, "offset": res.end,
                                  "session_id": sid, "cwd": red.redact(wd) if wd else wd}   # 커서에도 가린 값만
                processed += 1
                if processed % flush_every == 0:
                    save_cursor()
        finally:
            save_cursor()
    _write_atomic(root / "collect-stats.json", json.dumps(asdict(stats)))
    return stats

def estimate(days: int, now: float | None = None, sources: dict | None = None) -> dict:
    """첫 실행 동의 화면용 규모. 동의 전이라 파싱하지 않고 파일 수와 크기만 센다."""
    now = now or _now()
    paths = dict(sources or load_config()["source_paths"])
    out = {"claude": {"files": 0, "bytes": 0}, "codex": {"files": 0, "bytes": 0}}
    for surface, _p, st in _discover(paths, now - days * 86400):
        out[surface]["files"] += 1
        out[surface]["bytes"] += st.st_size
    return out
