"""후보 검증·결정 원장. 리포트 렌더 정본은 hc/report.py(아래 재노출은 기존 import 경로 호환용)."""
from __future__ import annotations
import hashlib, json, re, time, unicodedata
from .config import data_dir
from .fsutil import append_line
from .report import render, incomplete_reasons, stats_corrupt, parse_errors  # noqa: F401 (기존 import 경로 호환 재노출)

FORMS = ("hook", "rule", "skill", "script", "agent", "memory")
CREATABLE = ("hook", "rule", "skill")
_REQ = ("id", "signal_ids", "form", "title", "evidence", "proposed_change", "expected_effect")
_ID = re.compile(r"^hc-\d{4}-\d{2}-\d{2}-\d{2}$")
_DATE_RANGE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$")
# 형태별 허용 경로(~/.claude 기준 상대 경로, spec §7 표의 쓰는 곳만).
# script·agent·memory는 쓰는 곳이 없어(추천 전용) 경로 규칙을 두지 않는다. 리포트에는 초안 요약만 나온다.
_ID_RX = r"hc-\d{4}-\d{2}-\d{2}-\d{2}"
_PATHS = {
    "hook": re.compile(rf"^hooks/harness-creator/{_ID_RX}\.sh$"),
    "rule": re.compile(r"^rules/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"),
    "skill": re.compile(r"^skills/[A-Za-z0-9][A-Za-z0-9._-]*/SKILL\.md$"),
}

def _str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""

def _str_list(v) -> bool:
    return isinstance(v, list) and bool(v) and all(_str(x) for x in v)

def _index_signals(signals) -> dict:
    """신호 목록을 id → (samples 집합, sessions 집합)으로. 형식이 깨진 신호는 빈 집합으로 둔다."""
    out = {}
    for s in signals if isinstance(signals, list) else []:
        if not isinstance(s, dict) or not _str(s.get("id")):
            continue
        samples = s.get("samples") if isinstance(s.get("samples"), list) else []
        sessions = s.get("sessions") if isinstance(s.get("sessions"), list) else []
        out[s["id"]] = ({x for x in samples if isinstance(x, str)}, {x for x in sessions if isinstance(x, str)})
    return out

def _grounding(sids, index) -> tuple:
    """후보 signal_ids 중 알려진 신호의 samples 합집합과 sessions 합집합."""
    samples, sessions = set(), set()
    for sid in sids if isinstance(sids, list) else []:
        if isinstance(sid, str) and sid in index:
            samples |= index[sid][0]
            sessions |= index[sid][1]
    return samples, sessions

def _check_evidence(i, ev, grounding) -> list:
    if not isinstance(ev, dict):
        return [f"[{i}] evidence must be a dict"]
    samples, sessions = grounding
    errs = []
    s = ev.get("sessions")
    if not isinstance(s, int) or isinstance(s, bool) or s < 1:
        errs.append(f"[{i}] evidence.sessions must be a positive integer: {s!r}")
    elif s > len(sessions):
        errs.append(f"[{i}] evidence.sessions {s} exceeds the {len(sessions)} sessions of its signals")
    dr = ev.get("date_range")
    if not (isinstance(dr, str) and _DATE_RANGE.fullmatch(dr)):
        errs.append(f"[{i}] evidence.date_range not in YYYY-MM-DD..YYYY-MM-DD form: {dr!r}")
    quotes = ev.get("quotes")
    if not _str_list(quotes):
        errs.append(f"[{i}] evidence.quotes must be a non-empty list of strings")
    else:
        bad = [q for q in quotes if q not in samples]
        if bad:
            errs.append(f"[{i}] evidence.quotes not found in the samples of its signals: {len(bad)}")
    return errs

def _path_ok(form, path: str) -> bool:
    if path.startswith(("/", "~")) or ".." in path or "\\" in path:
        return False
    # 제어 문자(개행·탭·NUL 등)와 유니코드 줄·문단 구분자는 경로에 둘 수 없다.
    # match()+$ 는 끝 개행 하나를 허용하므로 fullmatch 로 문자열 전체를 본다.
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in path):
        return False
    rx = _PATHS.get(form)
    return bool(rx and rx.fullmatch(path))

def _check_change(i, pc, form, cid) -> list:
    if not isinstance(pc, dict):
        return [f"[{i}] proposed_change must be a dict"]
    errs = []
    if pc.get("action") not in ("create", "modify"):
        errs.append(f"[{i}] proposed_change.action not allowed: {pc.get('action')!r}")
    if not _str(pc.get("summary")):
        errs.append(f"[{i}] proposed_change.summary must be a non-empty string")
    path = pc.get("path")
    if form in FORMS and form not in CREATABLE:
        if path is not None:
            errs.append(f"[{i}] form {form} is recommendation only and takes no proposed_change.path: {path!r}")
    elif not _str(path):
        errs.append(f"[{i}] proposed_change.path must be a non-empty string")
    elif form in FORMS and not _path_ok(form, path):
        errs.append(f"[{i}] proposed_change.path not allowed for form {form}: {path!r}")
    if form == "hook" and _str(path) and _str(cid) and path != f"hooks/harness-creator/{cid}.sh":
        errs.append(f"[{i}] hook proposed_change.path must be hooks/harness-creator/<candidate id>.sh: {path!r}")
    paths = pc.get("paths")
    if paths is not None and not (_str_list(paths) and all(
            not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in g) for g in paths)):
        errs.append(f"[{i}] proposed_change.paths must be a non-empty list of strings")
    return errs

def _check_one(i, c, index) -> list:
    errs = []
    for k in _REQ:
        if k not in c:
            errs.append(f"[{i}] missing field: {k}")
    for k in ("id", "title", "expected_effect"):
        if k in c and not _str(c[k]):
            errs.append(f"[{i}] {k} must be a non-empty string")
    if _str(c.get("id")) and not _ID.fullmatch(c["id"]):
        errs.append(f"[{i}] id not in hc-YYYY-MM-DD-NN form: {c['id']!r}")
    form = c.get("form")
    if not (isinstance(form, str) and form in FORMS):
        errs.append(f"[{i}] form not allowed: {form!r}")
    sids = c.get("signal_ids")
    errs += _check_evidence(i, c.get("evidence"), _grounding(sids, index))
    if not _str_list(sids):
        errs.append(f"[{i}] signal_ids must be a non-empty list of strings")
    else:
        bad = [s for s in sids if s not in index]
        if bad:
            errs.append(f"[{i}] signal_ids not in the signal file: {bad}")
    ov = c.get("overlaps")
    if ov is not None and not (isinstance(ov, list) and all(isinstance(x, str) for x in ov)):
        errs.append(f"[{i}] overlaps must be a list of strings")
    errs += _check_change(i, c.get("proposed_change"), form, c.get("id"))
    return errs

def check_each(data, signals: list):
    """(최상위 오류 목록, [(후보, 그 후보의 오류 목록)]). 최상위가 깨졌으면 후보 목록은 비어 있다."""
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        return ["top level must be {\"candidates\": [...]}"], []
    index = _index_signals(signals)
    out, seen = [], set()
    for i, c in enumerate(data["candidates"]):
        if not isinstance(c, dict):
            out.append((c, [f"[{i}] not a dict"])); continue
        errs = _check_one(i, c, index)
        cid = c.get("id")
        if _str(cid):
            if cid in seen:
                errs.append(f"[{i}] duplicate candidate id: {cid}")
            seen.add(cid)
        out.append((c, errs))
    return [], out

def validate(data, signals: list) -> list:
    """LLM 출력과 리포트 사이의 유일한 관문. 예외를 던지지 않고 오류 목록만 돌려준다.
    signals는 신호 파일의 signals 목록이다. 인용·세션 수를 그 신호에 대조한다."""
    top, each = check_each(data, signals)
    return top + [e for _, errs in each for e in errs]

def _latest_candidates():
    d = data_dir() / "reports"
    files = sorted(d.glob("*.candidates.json")) if d.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else {"candidates": []}

def _decisions_path():
    return data_dir() / "decisions.jsonl"

def fingerprint(c: dict) -> str:
    """후보 제안 내용 지문(정규 JSON의 sha1 앞 8자). 결정은 id가 아니라 이 값에 묶인다.
    - 같은 날 재실행에서 id가 재사용돼도 결정이 다른 후보로 번지지 않는다.
    - 형태·제목·신호(정렬)·변경(action·path·summary·paths)·상시 로드(global_rule)·기대 효과 중 하나라도 바뀌면
      새 지문이라 다시 미결이 된다. 로드 범위가 바뀐 후보에 옛 승인이 이어지면 안 되기 때문이다(적용 단계가 이 지문을 본다).
    - 근거(evidence: 인용·세션 수·기간)는 매주 바뀌므로 넣지 않는다. 신호 단위 억제는 detect.suppress가 맡는다."""
    pc = c.get("proposed_change") if isinstance(c.get("proposed_change"), dict) else {}
    sids = c.get("signal_ids") if isinstance(c.get("signal_ids"), list) else []
    paths = pc.get("paths")
    canon = {"form": c.get("form"), "title": c.get("title"),
             "signal_ids": sorted(str(x) for x in sids), "global_rule": bool(c.get("global_rule")),
             "proposed_change": {"action": pc.get("action"), "path": pc.get("path"), "summary": pc.get("summary"),
                                 "paths": sorted(str(x) for x in paths) if isinstance(paths, list) else paths},
             "expected_effect": c.get("expected_effect")}
    text = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]

def _complete_record(rec) -> bool:
    """후보 단위 결정 레코드가 온전한가. 옛 형식(신호별 줄)·일부 필드만 남은 줄은 아니다."""
    return (isinstance(rec, dict)
            and isinstance(rec.get("candidate_id"), str)
            and isinstance(rec.get("fingerprint"), str) and len(rec["fingerprint"]) == 8
            and rec.get("decision") in ("approve", "reject")
            and isinstance(rec.get("signal_ids"), list) and bool(rec["signal_ids"])
            and isinstance(rec.get("signal_counts"), dict))

def _reopen_record(rec) -> bool:
    """승인 뒤 undo·쓰기 실패로 승인을 거둔 기록. 이 지문은 다시 미결이 되고 신호 억제도 풀린다."""
    return (isinstance(rec, dict) and rec.get("decision") == "reopen"
            and isinstance(rec.get("fingerprint"), str) and len(rec["fingerprint"]) == 8
            and isinstance(rec.get("signal_ids"), list))

def _records(text: str):
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _complete_record(rec) or _reopen_record(rec):
            yield rec

def _decided_fingerprints(text: str) -> set:
    """해석되는 온전한 레코드의 지문만. 중단된 쓰기로 잘린 줄은 미결로 남긴다. reopen 은 그 지문을 미결로 되돌린다."""
    out = set()
    for rec in _records(text):
        if rec["decision"] == "reopen":
            out.discard(rec["fingerprint"])
        else:
            out.add(rec["fingerprint"])
    return out

def pending() -> list:
    p = _decisions_path()
    done = _decided_fingerprints(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else set()
    return [c for c in _latest_candidates()["candidates"] if fingerprint(c) not in done]

def _signal_counts() -> dict:
    """억제 비교는 신호 count 단위로 한다(후보 evidence.sessions와 단위가 다르다)."""
    d = data_dir() / "signals"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return {}
    return {s["id"]: s["count"] for s in json.loads(files[-1].read_text(encoding="utf-8"))["signals"]}

def decide(candidate_id: str, decision: str, reason: str) -> None:
    if decision not in ("approve", "reject"):
        raise ValueError(decision)
    counts = _signal_counts()
    c = next((c for c in _latest_candidates()["candidates"] if c["id"] == candidate_id), None)
    if c is None:
        raise ValueError(f"no such candidate id: {candidate_id}")
    sids = list(c["signal_ids"])
    rec = {"candidate_id": candidate_id, "fingerprint": fingerprint(c), "signal_ids": sids,
           "signal_counts": {sid: counts.get(sid, 0) for sid in sids}, "decision": decision,
           "reason": reason, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    append_line(_decisions_path(), (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))

def reopen(candidate_id: str, reason: str) -> bool:
    """이 후보의 마지막 결정이 approve면 reopen 기록을 남긴다(승인 억제 해제). 남겼으면 True."""
    p = _decisions_path()
    if not p.exists():
        return False
    last = None
    for rec in _records(p.read_text(encoding="utf-8", errors="replace")):
        if rec.get("candidate_id") == candidate_id:
            last = rec
    if last is None or last["decision"] != "approve":
        return False
    rec = {"candidate_id": candidate_id, "fingerprint": last["fingerprint"], "signal_ids": list(last["signal_ids"]),
           "decision": "reopen", "reason": reason, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    append_line(p, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    return True

def find_candidate(candidate_id: str):
    return next((c for c in _latest_candidates()["candidates"] if isinstance(c, dict) and c.get("id") == candidate_id), None)

def is_approved(c: dict) -> bool:
    """이 후보(지문까지 같은 것)에 대한 마지막 온전한 결정이 approve인가."""
    p = _decisions_path()
    if not p.exists():
        return False
    fp, last = fingerprint(c), None
    for rec in _records(p.read_text(encoding="utf-8", errors="replace")):
        if rec["fingerprint"] == fp:
            last = rec["decision"]
    return last == "approve"
