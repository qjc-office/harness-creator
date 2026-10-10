import json
import pytest
from hc.candidates import validate, render, pending, decide

def cand(**kw):
    c = {"id": "hc-1", "signal_ids": ["failure_loop:a"], "form": "hook", "title": "t",
         "evidence": {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q"]}, "overlaps": [],
         "proposed_change": {"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-01.sh", "summary": "s"}, "expected_effect": "e"}
    c.update(kw); return c

SIGNALS = [{"id": "failure_loop:a", "samples": ["q", "q2"], "sessions": ["s1", "s2"]},
           {"id": "correction:b", "samples": ["c1"], "sessions": ["s3"]}]

def vcand(**kw):
    """validate 대상 후보: id 형식까지 맞춘 것."""
    kw.setdefault("id", "hc-2026-10-09-01")
    return cand(**kw)

def test_valid():
    assert validate({"candidates": [vcand()]}, SIGNALS) == []

def test_unknown_signal_rejected():
    assert any("signal" in e for e in validate({"candidates": [vcand(signal_ids=["nope"])]}, SIGNALS))

def test_bad_form_and_missing_evidence():
    errs = validate({"candidates": [vcand(form="plugin", evidence={})]}, SIGNALS)
    assert any("form" in e for e in errs) and any("evidence" in e for e in errs)

def test_not_a_dict():
    assert validate([], []) != []

def test_invalid_candidates_marks_report_failed():
    md = render(None, {"events": {"claude": 10, "codex": 5}, "files": 3, "records": 100, "unknown": 1}, 4, "검증 실패: form")
    assert "실패" in md.splitlines()[0] and "후보 없음" not in md

def test_zero_candidates_says_what_was_seen():
    md = render({"candidates": []}, {"events": {"claude": 10, "codex": 5}, "files": 3, "records": 100, "unknown": 0}, 0, None)
    assert "후보 없음" in md and "claude 10" in md and "codex 5" in md

def test_unknown_ratio_warning_on_top():
    md = render({"candidates": []}, {"events": {"claude": 1, "codex": 0}, "files": 1, "records": 100, "unknown": 9}, 0, None)
    assert "경고" in md.split("\n## ")[0]

LEX_WARN = "> 추가 가림 목록 없음."

def _stats(**kw):
    s = {"events": {"claude": 10, "codex": 5}, "files": 3, "records": 100, "unknown": 0}
    s.update(kw); return s

def test_lexicon_missing_warning_on_top():
    md = render({"candidates": [cand()]}, _stats(lexicon=False), 1, None)
    assert LEX_WARN in md.split("\n## ")[0]

def test_parse_errors_warning_on_top():
    md = render({"candidates": [cand()]}, _stats(errors=2), 1, None)
    assert "> 경고: 파싱 실패 파일 2개." in md.split("\n## ")[0]

def test_warnings_on_top_of_failed_report():
    md = render(None, _stats(lexicon=False, errors=1), 0, "검증 실패: form")
    assert LEX_WARN in md and "> 경고: 파싱 실패 파일 1개." in md

def test_no_lexicon_or_error_warning_when_clean():
    md = render({"candidates": [cand()]}, _stats(lexicon=True, errors=0), 1, None)
    assert "고객명 사전" not in md and "파싱 실패" not in md
    md2 = render({"candidates": []}, _stats(), 0, None)
    assert "고객명 사전" not in md2 and "파싱 실패" not in md2

def test_pending_and_decide(cache):
    (cache / "reports").mkdir(parents=True)
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": [cand(), cand(id="hc-2", signal_ids=["correction:b"])]}))
    assert [c["id"] for c in pending()] == ["hc-1", "hc-2"]
    decide("hc-1", "reject", "중복")
    assert [c["id"] for c in pending()] == ["hc-2"]
    line = json.loads((cache / "decisions.jsonl").read_text().splitlines()[0])
    assert line["signal_ids"] == ["failure_loop:a"] and line["decision"] == "reject"

def test_decide_stores_signal_count(cache):
    (cache / "reports").mkdir(parents=True); (cache / "signals").mkdir(parents=True)
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": [cand()]}))
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [
        {"id": "failure_loop:a", "type": "failure_loop", "key": "k", "sessions": 3, "count": 7,
         "first_seen": "a", "last_seen": "b", "samples": [], "surfaces": [], "context": {}}]}))
    decide("hc-1", "approve", "좋음")
    line = json.loads((cache / "decisions.jsonl").read_text().splitlines()[0])
    assert line["signal_counts"] == {"failure_loop:a": 7}
    assert set(line) == {"candidate_id", "fingerprint", "signal_ids", "signal_counts", "decision", "reason", "ts"}

# --- validate는 절대 예외를 던지지 않는다 ---
import pytest

SIG = SIGNALS

def _errs(**kw):
    return validate({"candidates": [vcand(**kw)]}, SIG)

def test_missing_summary_is_error():
    assert any("summary" in e for e in _errs(proposed_change={"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-01.sh"}))

@pytest.mark.parametrize("kw", [
    {"evidence": ["q"]},
    {"proposed_change": "create hooks/harness-creator/hc-2026-10-09-01.sh"},
    {"signal_ids": 3},
    {"signal_ids": [{"x": 1}]},
    {"signal_ids": [["nested"]]},
    {"overlaps": "rules/x.md"},
    {"id": 5},
    {"title": ""},
    {"expected_effect": None},
])
def test_bad_shapes_are_errors_not_exceptions(kw):
    assert _errs(**kw) != []

def test_non_dict_candidate_is_error():
    assert validate({"candidates": ["x", 1, None]}, SIG) != []

def test_quotes_as_str_is_error():
    assert any("quotes" in e for e in _errs(evidence={"sessions": 2, "quotes": "q"}))

def test_quotes_with_empty_item_is_error():
    assert any("quotes" in e for e in _errs(evidence={"sessions": 2, "quotes": ["q", ""]}))

@pytest.mark.parametrize("s", [0, -1, True, "2", 1.5])
def test_sessions_must_be_positive_int(s):
    assert any("sessions" in e for e in _errs(evidence={"sessions": s, "quotes": ["q"]}))

def test_duplicate_ids_rejected():
    errs = validate({"candidates": [vcand(), vcand()]}, SIG)
    assert any("중복" in e and "hc-2026-10-09-01" in e for e in errs)

def test_render_none_without_error_is_failure():
    md = render(None, _stats(), 0, None)
    first = md.splitlines()[0]
    assert "실패" in first and "후보 없음" not in md and "후보 파일 없음" in md

def test_render_survives_malformed_candidate():
    md = render({"candidates": [{"id": "hc-9"}, "x", cand(evidence=[], proposed_change="s", overlaps=None)]}, _stats(), 1, None)
    assert "hc-9" in md

def test_decide_unknown_id_raises_value_error(cache):
    (cache / "reports").mkdir(parents=True)
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": [cand()]}))
    with pytest.raises(ValueError, match="후보 id 없음: hc-404"):
        decide("hc-404", "approve", "r")

def test_pending_skips_broken_decision_line(cache):
    (cache / "reports").mkdir(parents=True)
    (cache / "reports" / "2026-10-09.candidates.json").write_text(json.dumps({"candidates": [cand(), cand(id="hc-2", signal_ids=["correction:b"])]}))
    decide("hc-1", "reject", "중복")
    with (cache / "decisions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"candidate_id": "hc-2", "sig\n[1, 2]\n')
    assert [c["id"] for c in pending()] == ["hc-2"]

# --- 근거 대조와 경로 형식 ---
def test_date_range_format_enforced():
    assert any("date_range" in e for e in _errs(evidence={"sessions": 2, "date_range": "x..y", "quotes": ["q"]}))
    assert any("date_range" in e for e in _errs(evidence={"sessions": 2, "quotes": ["q"]}))
    assert _errs(evidence={"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q"]}) == []

def test_quote_must_come_from_signal_samples():
    assert any("quotes" in e for e in _errs(evidence={"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["지어낸 문장"]}))
    # 부분 문자열은 인용으로 인정하지 않는다
    assert any("quotes" in e for e in _errs(evidence={"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q2 "]}))

def test_quotes_from_union_of_signal_samples():
    ok = _errs(signal_ids=["failure_loop:a", "correction:b"],
               evidence={"sessions": 3, "date_range": "2026-10-01..2026-10-07", "quotes": ["q2", "c1"]})
    assert ok == []

def test_sessions_cannot_exceed_signal_sessions():
    assert any("sessions" in e for e in _errs(evidence={"sessions": 3, "date_range": "2026-10-01..2026-10-07", "quotes": ["q"]}))

@pytest.mark.parametrize("cid", ["hc-1", "hc-2026-10-09-1", "hc-2026-10-09-001", "x-2026-10-09-01", "hc-2026-10-09-01 "])
def test_candidate_id_format(cid):
    assert any("id" in e for e in _errs(id=cid))

@pytest.mark.parametrize("form,path", [
    ("hook", "hooks/harness-creator/hc-2026-10-09-01.sh"),
    ("rule", "rules/foo.md"),
    ("skill", "skills/foo/SKILL.md"),
])
def test_allowed_paths_by_form(form, path):
    assert _errs(form=form, proposed_change={"action": "create", "path": path, "summary": "s"}) == []

@pytest.mark.parametrize("form,path", [
    ("hook", "rules/foo.md"),
    ("hook", "hooks/foo-guard.sh"),
    ("hook", "hooks/harness-creator/hc-2026-10-09-02.sh"),
    ("hook", "hooks/"),
    ("rule", "hooks/foo.sh"),
    ("skill", "skills/foo/bar.md"),
    ("skill", "skills/foo"),
    ("skill", "skills/foo/sub/SKILL.md"),
    ("script", "hooks/x.py"),
    ("agent", "rules/a.md"),
    ("memory", "memory/feedback_foo.md"),
    ("memory", "feedback_foo.txt"),
    ("hook", "/Users/x/hooks/harness-creator/hc-2026-10-09-01.sh"),
    ("hook", "hooks/../settings.json"),
    ("rule", "~/rules/foo.md"),
    ("memory", "~foo.md"),
    ("memory", "..md"),
])
def test_disallowed_paths_by_form(form, path):
    errs = _errs(form=form, proposed_change={"action": "create", "path": path, "summary": "s"})
    assert any("path" in e for e in errs), (form, path)

def test_validate_tolerates_malformed_signals():
    assert validate({"candidates": [vcand()]}, [None, 3, {"id": 5}, {"id": "failure_loop:a", "samples": "q", "sessions": None}]) != []

# --- 본 범위는 탐지가 본 것, 증분 수집은 따로 ---
SCOPE = {"days": 7, "sessions": 12, "events": {"claude": 300, "codex": 40}, "skipped": 2,
         "earliest": "2026-10-02T00:00:00Z", "latest": "2026-10-08T00:00:00Z"}

def test_scope_line_shows_detect_scope():
    md = render({"candidates": []}, _stats(), 4, None, scope=SCOPE)
    assert "본 범위: 최근 7일, 세션 12개, 이벤트 claude 300 / codex 40, 신호 4개 (건너뛴 줄 2)" in md
    assert "이번 증분 수집: 파일 3개, 이벤트 claude 10 / codex 5" in md

def test_scope_line_on_failed_report():
    md = render(None, _stats(), 4, "검증 실패", scope=SCOPE)
    assert "실패" in md.splitlines()[0]
    assert "본 범위: 최근 7일, 세션 12개" in md and "이번 증분 수집: 파일 3개" in md

def test_scope_missing_is_said():
    md = render({"candidates": []}, _stats(), 0, None)
    assert "본 범위: 신호 파일 없음" in md

# --- 사전 하한 경고는 lexicon_min_terms를 켠 설정에서만(test_report가 덮는다). 공개 기본은 안내 한 줄 ---
def test_missing_lexicon_info_only():
    md = render({"candidates": []}, _stats(lexicon=False, lexicon_terms=0), 0, None)
    assert LEX_WARN in md and "하한" not in md

@pytest.mark.parametrize("form", ["script", "agent", "memory"])
def test_recommend_only_forms_have_no_path(form):
    assert _errs(form=form, proposed_change={"action": "create", "summary": "s"}) == []
    errs = _errs(form=form, proposed_change={"action": "create", "path": "scripts/foo.py", "summary": "s"})
    assert any("path" in e for e in errs)

# --- 결정은 후보 내용(fingerprint)에 묶이고, 후보당 한 줄을 한 번에 쓴다 ---
import hashlib, os
from hc import candidates as _cm

def _fp(form, path, sids, title="t", action="create", summary="s", effect="e", global_rule=False, paths=None):
    """판정 공식: 근거(evidence)를 뺀 제안 내용의 정규 JSON sha1 앞 8자. 기본값은 cand()와 같다.
    Task 10 fix: 상시 로드 여부(global_rule)와 로드 조건(proposed_change.paths)도 지문에 넣는다."""
    canon = {"form": form, "title": title, "signal_ids": sorted(sids), "global_rule": global_rule,
             "proposed_change": {"action": action, "path": path, "summary": summary, "paths": paths},
             "expected_effect": effect}
    return hashlib.sha1(json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:8]

def _write_cands(cache, day, items):
    (cache / "reports").mkdir(parents=True, exist_ok=True)
    (cache / "reports" / f"{day}.candidates.json").write_text(json.dumps({"candidates": items}))

def _dec_lines(cache):
    return (cache / "decisions.jsonl").read_text(encoding="utf-8").splitlines()

CID = "hc-2026-10-09-01"

def test_fingerprint_formula():
    c = cand(signal_ids=["correction:b", "failure_loop:a"])
    assert _cm.fingerprint(c) == _fp("hook", "hooks/harness-creator/hc-2026-10-09-01.sh", ["correction:b", "failure_loop:a"])
    # signal_ids 순서는 지문에 영향이 없다
    assert _cm.fingerprint(cand(signal_ids=["failure_loop:a", "correction:b"])) == _cm.fingerprint(c)

@pytest.mark.parametrize("other", [
    {"proposed_change": {"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-02.sh", "summary": "s"}},   # 경로가 다름
    {"signal_ids": ["correction:b"]},                                                  # 신호가 다름
    {"form": "rule", "proposed_change": {"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-01.sh", "summary": "s"}},  # 형태가 다름
])
def test_same_id_different_candidate_stays_pending(cache, other):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    decide(CID, "reject", "중복")
    assert pending() == []
    # 같은 날 재실행: 새 후보가 같은 id를 재사용한다
    _write_cands(cache, "2026-10-09", [cand(id=CID, **other)])
    assert [c["id"] for c in pending()] == [CID]

def test_same_content_new_id_stays_decided(cache):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    decide(CID, "approve", "좋음")
    _write_cands(cache, "2026-10-09", [cand(id="hc-2026-10-09-02"), cand(id=CID, signal_ids=["correction:b"])])
    assert [c["id"] for c in pending()] == [CID]

def test_decide_writes_one_line_per_candidate_with_single_write(cache, monkeypatch):
    (cache / "signals").mkdir(parents=True)
    (cache / "signals" / "2026-10-09.json").write_text(json.dumps({"generated_at": "x", "signals": [
        {"id": "failure_loop:a", "count": 7}, {"id": "correction:b", "count": 3}]}))
    _write_cands(cache, "2026-10-09", [cand(id=CID, signal_ids=["failure_loop:a", "correction:b"])])
    calls = []
    real = os.write
    from hc import fsutil as _fs   # 한 번의 write는 이제 fsutil.append_line이 맡는다
    monkeypatch.setattr(_fs.os, "write", lambda fd, b: calls.append(b) or real(fd, b))
    decide(CID, "reject", "중복")
    assert len(calls) == 1
    lines = _dec_lines(cache)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["fingerprint"] == _fp("hook", "hooks/harness-creator/hc-2026-10-09-01.sh", ["failure_loop:a", "correction:b"])
    assert rec["signal_ids"] == ["failure_loop:a", "correction:b"]
    assert rec["signal_counts"] == {"failure_loop:a": 7, "correction:b": 3}
    assert oct(os.stat(cache / "decisions.jsonl").st_mode & 0o777) == "0o600"

def _full_record(c, decision="reject"):
    return json.dumps({"candidate_id": c["id"], "fingerprint": _cm.fingerprint(c), "signal_ids": c["signal_ids"],
                       "signal_counts": {s: 1 for s in c["signal_ids"]}, "decision": decision,
                       "reason": "r", "ts": "2026-10-09T00:00:00"}, ensure_ascii=False)

@pytest.mark.parametrize("cut", [10, 40, -1])
def test_truncated_record_does_not_hide_candidate(cache, cut):
    c = cand(id=CID)
    _write_cands(cache, "2026-10-09", [c])
    full = _full_record(c)
    (cache / "decisions.jsonl").write_text(full[:cut], encoding="utf-8")
    assert [x["id"] for x in pending()] == [CID]

@pytest.mark.parametrize("drop", ["fingerprint", "decision", "signal_ids", "signal_counts"])
def test_record_missing_field_does_not_hide_candidate(cache, drop):
    c = cand(id=CID)
    _write_cands(cache, "2026-10-09", [c])
    rec = json.loads(_full_record(c)); rec.pop(drop)
    (cache / "decisions.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    assert [x["id"] for x in pending()] == [CID]

def test_old_per_signal_record_does_not_hide_by_id_alone(cache):
    # 옛 형식(신호별 줄, 지문 없음)은 후보를 숨기는 근거가 아니다(억제 계산에서만 읽는다)
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    (cache / "decisions.jsonl").write_text(json.dumps(
        {"candidate_id": CID, "signal_id": "failure_loop:a", "decision": "reject", "reason": "", "count": 2, "ts": "t"}) + "\n")
    assert [x["id"] for x in pending()] == [CID]

def test_decide_after_truncated_tail_starts_new_line(cache):
    c1, c2 = cand(id=CID), cand(id="hc-2026-10-09-02", signal_ids=["correction:b"])
    _write_cands(cache, "2026-10-09", [c1, c2])
    (cache / "decisions.jsonl").write_text(_full_record(c1)[:30], encoding="utf-8")
    decide(c2["id"], "approve", "좋음")
    assert [x["id"] for x in pending()] == [CID]
    assert json.loads(_dec_lines(cache)[-1])["candidate_id"] == c2["id"]

# --- 파싱 실패가 있으면 리포트 첫 줄이 불완전을 말한다 ---
def _first(md):
    return md.splitlines()[0]

def test_parse_errors_mark_first_line_incomplete_with_candidates():
    md = render({"candidates": [cand()]}, _stats(errors=3), 1, None)
    assert _first(md) == "# harness-creator 리포트: 후보 1건 (불완전: 파싱 실패 파일 3개)"

def test_parse_errors_mark_first_line_incomplete_without_candidates():
    md = render({"candidates": []}, _stats(errors=1), 0, None)
    assert _first(md) == "# harness-creator 리포트: 후보 없음 (불완전: 파싱 실패 파일 1개)"

@pytest.mark.parametrize("errors", [0, None, "x", True, -1])
def test_no_incomplete_mark_without_real_errors(errors):
    st = _stats()
    if errors is None:
        st.pop("errors", None)
    else:
        st["errors"] = errors
    assert _first(render({"candidates": []}, st, 0, None)) == "# harness-creator 리포트: 후보 없음"

# --- P1: 경로 끝 개행·제어 문자는 허용 경로가 아니다 ---
@pytest.mark.parametrize("form,path", [
    ("hook", "hooks/x.sh\n"),
    ("hook", "hooks/x.sh\r"),
    ("hook", "hooks/x.sh\t"),
    ("hook", "hooks/x.sh\x00"),
    ("hook", "hooks/a\x1b.sh"),
    ("hook", "hooks/a\x7f.sh"),
    ("hook", "hooks/a\x85.sh"),
    ("rule", "rules/a.md\n"),
    ("skill", "skills/foo/\n"),
    ("memory", "a.md\n"),
    ("hook", "hooks/a .sh"),
])
def test_control_chars_in_path_rejected(form, path):
    errs = _errs(form=form, proposed_change={"action": "create", "path": path, "summary": "s"})
    assert any("path" in e for e in errs), (form, repr(path))

# --- 깨진 레코드 경고(리포트 머리, rc 영향 없음) ---
def test_malformed_warning_in_header():
    md = render({"candidates": []}, _stats(errors=0, malformed=3), 0, None)
    assert "> 경고: 깨진 레코드 3줄 건너뜀(형식 오류, 재시도 불가)." in md
    assert md.index("깨진 레코드") < (md.index("\n## ") if "\n## " in md else len(md))
    # r5 E4로 판정 변경: 깨진 레코드도 불완전이다(경고 줄은 유지)
    assert _first(md) == "# harness-creator 리포트: 후보 없음 (불완전: 깨진 레코드 3줄)"

@pytest.mark.parametrize("m", [0, None, "3", True, -2])
def test_no_malformed_warning_without_real_count(m):
    st = _stats(errors=0)
    if m is not None:
        st["malformed"] = m
    assert "깨진 레코드" not in render({"candidates": []}, st, 0, None)

# --- 파싱 실패 수가 없거나 손상이면 0이 아니라 '알 수 없음'(None) ---
from hc.candidates import parse_errors

@pytest.mark.parametrize("st", [{}, {"errors": "2"}, {"errors": -1}, {"errors": True}, {"errors": None}, {"errors": 1.0}, None, []])
def test_parse_errors_invalid_is_none(st):
    assert parse_errors(st) is None

@pytest.mark.parametrize("n", [0, 2])
def test_parse_errors_valid_int(n):
    assert parse_errors({"errors": n}) == n

def test_corrupt_stats_warning_in_header():
    md = render({"candidates": []}, {"events": {}, "errors": "2"}, 0, None)
    assert "> 경고: 수집 통계 손상(오류 수를 읽지 못함). 이번 실행은 성공으로 기록되지 않는다." in md
    assert "수집 통계 손상" not in render({"candidates": []}, _stats(errors=0, deferred=0, malformed=0), 0, None)
    assert "수집 통계 손상" in render({"candidates": []}, _stats(errors=0), 0, None)   # deferred 없음도 손상

# --- 후보 id·date_range도 전체 일치(끝 개행 거부) ---
@pytest.mark.parametrize("cid", ["hc-2026-10-09-01\n", "hc-2026-10-09-01\r", "hc-2026-10-09-01\x00"])
def test_candidate_id_trailing_control_rejected(cid):
    assert any("id 형식" in e for e in _errs(id=cid))

@pytest.mark.parametrize("dr", ["2026-10-01..2026-10-07\n", "2026-10-01..2026-10-07\t"])
def test_date_range_trailing_control_rejected(dr):
    ev = {"sessions": 2, "date_range": dr, "quotes": ["q"]}
    assert any("date_range" in e for e in _errs(evidence=ev))

# --- skill 경로는 정확히 skills/<이름>/SKILL.md ---
@pytest.mark.parametrize("path", ["skills/foo/", "skills/foo/skill.md", "skills//SKILL.md", "skills/foo/SKILL.md/"])
def test_skill_path_must_be_skill_md(path):
    errs = _errs(form="skill", proposed_change={"action": "create", "path": path, "summary": "s"})
    assert any("path" in e for e in errs), path

def test_skill_md_path_accepted():
    assert _errs(form="skill", proposed_change={"action": "modify", "path": "skills/foo-bar/SKILL.md", "summary": "s"}) == []

# --- 미처리(deferred) 파일도 불완전이다. 판정은 한 함수(incomplete_reasons)가 한다 ---
from hc.candidates import incomplete_reasons, stats_corrupt

def test_deferred_marks_first_line_incomplete():
    md = render({"candidates": []}, _stats(errors=0, deferred=5, malformed=0), 0, None)
    assert _first(md) == "# harness-creator 리포트: 후보 없음 (불완전: 미처리 파일 5개)"

def test_errors_and_deferred_listed_together():
    md = render({"candidates": [cand()]}, _stats(errors=2, deferred=5, malformed=0), 1, None)
    assert _first(md) == "# harness-creator 리포트: 후보 1건 (불완전: 파싱 실패 파일 2개, 미처리 파일 5개)"

def test_incomplete_reasons_lang_variants():
    st = _stats(errors=1, deferred=2, malformed=0)
    assert incomplete_reasons(st) == ["파싱 실패 파일 1개", "미처리 파일 2개"]
    assert incomplete_reasons(st, lang="en") == ["parse errors 1", "deferred files 2"]
    assert incomplete_reasons(_stats(errors=0, deferred=0, malformed=0)) == []

@pytest.mark.parametrize("deferred", ["3", -1, True, None, 2.0])
def test_bad_deferred_is_corrupt(deferred):
    st = _stats(errors=0, malformed=0)
    if deferred is not None:
        st["deferred"] = deferred
    assert stats_corrupt(st) is True

def test_clean_counts_not_corrupt():
    assert stats_corrupt(_stats(errors=0, deferred=0, malformed=0)) is False

# --- 결정 원장이 이미 0644여도 덧붙일 때 0600으로 조인다 ---
def test_existing_decisions_file_tightened(cache):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    d = cache / "decisions.jsonl"
    d.write_text(""); os.chmod(d, 0o644)
    decide(CID, "reject", "중복")
    assert oct(os.stat(d).st_mode & 0o777) == "0o600"

def test_new_decisions_file_0600_under_umask_022(cache):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    old = os.umask(0o022)
    try:
        decide(CID, "approve", "좋음")
    finally:
        os.umask(old)
    assert oct(os.stat(cache / "decisions.jsonl").st_mode & 0o777) == "0o600"

# --- 깨진 레코드(malformed)도 불완전이다 ---
def test_malformed_marks_first_line_incomplete_and_keeps_warning():
    md = render({"candidates": []}, _stats(errors=0, deferred=0, malformed=2), 0, None)
    assert _first(md) == "# harness-creator 리포트: 후보 없음 (불완전: 깨진 레코드 2줄)"
    assert "> 경고: 깨진 레코드 2줄 건너뜀(형식 오류, 재시도 불가)." in md

def test_all_three_reasons_in_order():
    st = _stats(errors=1, deferred=2, malformed=3)
    assert incomplete_reasons(st) == ["파싱 실패 파일 1개", "미처리 파일 2개", "깨진 레코드 3줄"]
    assert incomplete_reasons(st, lang="en") == ["parse errors 1", "deferred files 2", "malformed lines 3"]

@pytest.mark.parametrize("m", ["1", -1, True, None])
def test_bad_malformed_is_corrupt(m):
    st = _stats(errors=0, deferred=0)
    if m is not None:
        st["malformed"] = m
    assert stats_corrupt(st) is True

# --- 제안 내용이 바뀌면 지문이 바뀌어 다시 미결이 된다. 근거(evidence)는 지문에 넣지 않는다 ---
@pytest.mark.parametrize("change", [
    {"proposed_change": {"action": "modify", "path": "hooks/harness-creator/hc-2026-10-09-01.sh", "summary": "s"}},   # action
    {"proposed_change": {"action": "create", "path": "hooks/harness-creator/hc-2026-10-09-01.sh", "summary": "다른 요약"}},   # summary
    {"title": "다른 제목"},
    {"expected_effect": "다른 기대 효과"},
])
def test_changed_proposal_is_pending_again(cache, change):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    decide(CID, "approve", "좋음")
    assert pending() == []
    _write_cands(cache, "2026-10-16", [cand(id="hc-2026-10-16-01", **change)])
    assert [c["id"] for c in pending()] == ["hc-2026-10-16-01"]

@pytest.mark.parametrize("evidence", [
    {"sessions": 5, "date_range": "2026-10-10..2026-10-16", "quotes": ["q2"]},
    {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q", "q2"]},
])
def test_evidence_only_change_stays_decided(cache, evidence):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    decide(CID, "reject", "중복")
    _write_cands(cache, "2026-10-16", [cand(id="hc-2026-10-16-01", evidence=evidence)])
    assert pending() == []

def test_signal_ids_order_irrelevant_in_new_formula():
    a = cand(signal_ids=["correction:b", "failure_loop:a"])
    b = cand(signal_ids=["failure_loop:a", "correction:b"])
    assert _cm.fingerprint(a) == _cm.fingerprint(b) == _fp("hook", "hooks/harness-creator/hc-2026-10-09-01.sh", ["correction:b", "failure_loop:a"])

def test_old_formula_record_no_longer_hides(cache):
    # 옛 지문(form|path|signal_ids)으로 기록된 결정은 새 지문과 달라 후보를 숨기지 않는다(의도된 효과)
    c = cand(id=CID)
    _write_cands(cache, "2026-10-09", [c])
    old_fp = hashlib.sha1("hook|hooks/harness-creator/hc-2026-10-09-01.sh|failure_loop:a".encode()).hexdigest()[:8]
    assert old_fp != _cm.fingerprint(c)
    (cache / "decisions.jsonl").write_text(json.dumps(
        {"candidate_id": CID, "fingerprint": old_fp, "signal_ids": ["failure_loop:a"],
         "signal_counts": {"failure_loop:a": 3}, "decision": "reject", "reason": "r", "ts": "t"}) + "\n")
    assert [x["id"] for x in pending()] == [CID]

@pytest.mark.parametrize("bad", [{"proposed_change": "s"}, {"proposed_change": None}, {"signal_ids": "x"}, {"title": 3}])
def test_fingerprint_tolerates_malformed_candidate(bad):
    assert len(_cm.fingerprint(cand(**bad))) == 8

# --- Task 8: paths 조건, 후보 찾기, 승인 여부 ---
def _rule_pc(**kw):
    pc = {"action": "create", "path": "rules/foo.md", "summary": "s"}
    pc.update(kw); return pc

def test_rule_paths_list_accepted():
    assert _errs(form="rule", proposed_change=_rule_pc(paths=["src/**", "*.py"])) == []

@pytest.mark.parametrize("paths", ["src/**", [], [""], ["  "], [3], ["a\nb"], ["a b"], None])
def test_rule_paths_shape(paths):
    pc = _rule_pc() if paths is None else _rule_pc(paths=paths)
    errs = _errs(form="rule", proposed_change=pc)
    assert (errs == []) if paths is None else any("paths" in e for e in errs)

@pytest.mark.parametrize("path", ["rules/.hidden.md", "rules/a/b.md", "rules/foo.txt", "skills/.x/SKILL.md"])
def test_rule_skill_slug_paths_rejected(path):
    form = "skill" if path.startswith("skills/") else "rule"
    assert any("path" in e for e in _errs(form=form, proposed_change=_rule_pc(path=path)))

def test_find_candidate(cache):
    _write_cands(cache, "2026-10-09", [cand(id=CID), "x"])
    assert _cm.find_candidate(CID)["id"] == CID
    assert _cm.find_candidate("hc-2026-10-09-99") is None

def test_is_approved_follows_last_decision(cache):
    c = cand(id=CID)
    _write_cands(cache, "2026-10-09", [c])
    assert _cm.is_approved(c) is False
    decide(CID, "approve", "r")
    assert _cm.is_approved(c) is True
    decide(CID, "reject", "다시 보니 아님")
    assert _cm.is_approved(c) is False

def test_is_approved_bound_to_fingerprint(cache):
    c = cand(id=CID)
    _write_cands(cache, "2026-10-09", [c])
    decide(CID, "approve", "r")
    changed = cand(id=CID, title="다른 제목")
    assert _cm.is_approved(changed) is False
    with (cache / "decisions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"candidate_id": "x", "fing\n')   # 잘린 줄은 무시
    assert _cm.is_approved(c) is True

def test_decisions_ledger_at_data_dir_root(cache):
    _write_cands(cache, "2026-10-09", [cand(id=CID)])
    decide(CID, "reject", "r")
    assert (cache / "decisions.jsonl").is_file() and not (cache / "reports" / "decisions.jsonl").exists()

def test_fingerprint_includes_global_rule_and_paths():
    base = cand()
    assert _cm.fingerprint(cand(global_rule=True)) != _cm.fingerprint(base)
    assert _cm.fingerprint(cand(global_rule=True)) == _fp("hook", "hooks/harness-creator/hc-2026-10-09-01.sh",
                                                          ["failure_loop:a"], global_rule=True)
    pc = dict(base["proposed_change"], paths=["src/**"])
    assert _cm.fingerprint(cand(proposed_change=pc)) == _fp("hook", "hooks/harness-creator/hc-2026-10-09-01.sh",
                                                            ["failure_loop:a"], paths=["src/**"])
    pc2 = dict(base["proposed_change"], paths=["src/**", "lib/**"])
    assert _cm.fingerprint(cand(proposed_change=pc2)) != _cm.fingerprint(cand(proposed_change=pc))
