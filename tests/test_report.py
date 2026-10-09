from hc.report import render, incomplete_reasons

ST = {"events": {"claude": 10, "codex": 5}, "files": 3, "records": 100, "unknown": 0, "errors": 0, "deferred": 0,
      "malformed": 0, "lexicon": True}
C = {"id": "hc-2026-10-09-01", "signal_ids": ["s"], "form": "rule", "title": "t",
     "evidence": {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q"]}, "overlaps": [],
     "proposed_change": {"action": "create", "path": "rules/a.md", "summary": "s"}, "expected_effect": "e"}

def test_titles_by_language():
    assert render({"candidates": [C]}, ST, 1, None, lang="ko").splitlines()[0] == "# harness-creator 리포트: 후보 1건"
    assert render({"candidates": []}, ST, 0, None, lang="en").splitlines()[0] == "# harness-creator report: no candidates"
    assert render({"candidates": []}, ST, 0, None, lang="zh").splitlines()[0] == "# harness-creator 报告: 无候选"

def test_cli_upgrade_first_line():
    md = render(None, ST, 0, "", lang="ko", error_kind="cli_upgrade")
    assert md.splitlines()[0] == "# harness-creator 리포트: 실패 (CLI 업그레이드 필요)"

def test_error_kind_body():
    md = render(None, ST, 0, "rc=3", lang="ko", error_kind="collect")
    assert "수집 실패 rc=3" in md

def test_no_extra_redaction_line():
    md = render({"candidates": []}, {**ST, "lexicon": False}, 0, None, lang="ko")
    assert "> 추가 가림 목록 없음." in md.split("\n## ")[0]
    assert "추가 가림" not in render({"candidates": []}, ST, 0, None, lang="ko")

def test_thin_warning_only_when_floor_configured(cache):
    from hc.config import save_user_config
    st = {**ST, "lexicon_terms": 3}
    assert "하한" not in render({"candidates": []}, st, 0, None, lang="ko")
    save_user_config({"redact": {"lexicon_min_terms": 10}})
    assert "> 경고: 추가 가림 목록 3건(하한 10)." in render({"candidates": []}, st, 0, None, lang="ko")

def test_sections_effect_codex_filtered():
    gates = {"codex_notes": [{**C, "id": "hc-2026-10-09-02", "title": "codex only"}],
             "filtered": [{"gate": "G1", "signal_id": "a"}, {"gate": "G1", "signal_id": "b"}, {"gate": "G5", "candidate_id": "x"}],
             "resume_skipped": 4}
    effects = [{"candidate_id": "hc-2026-10-01-01", "recurred": True, "last_seen": "2026-10-08T00:00:00Z"}]
    md = render({"candidates": [C]}, ST, 1, None, lang="ko", gates=gates, effects=effects)
    assert "## 적용 효과" in md and "- hc-2026-10-01-01: 재발 (마지막 발생 2026-10-08T00:00:00Z)" in md
    assert "## Codex 참고" in md and "hc-2026-10-09-02 codex only" in md
    assert "- 이미 해소된 신호: 2건" in md and "- 주간 한도 초과: 1건" in md and "- 재개 문구 발화: 4건" in md
    tail = [l for l in md.split("## 걸러진 신호", 1)[1].splitlines() if l.strip()]
    assert tail == ["- 이미 해소된 신호: 2건", "- 주간 한도 초과: 1건", "- 재개 문구 발화: 4건"]   # 사유와 건수만, id(a·b·x) 없음

def test_unknown_ratio_is_incomplete():
    st = {**ST, "unknown": 9}
    assert incomplete_reasons(st) == ["모르는 레코드 비율 9.0%"]
    assert render({"candidates": []}, st, 0, None, lang="ko").splitlines()[0] == \
        "# harness-creator 리포트: 후보 없음 (불완전: 모르는 레코드 비율 9.0%)"

def test_manual_forms_marked():
    md = render({"candidates": [{**C, "form": "memory", "proposed_change": {**C["proposed_change"], "path": "m.md"}}]},
                ST, 1, None, lang="ko")
    assert "- 자동 적용 대상 아님(초안과 적용 방법만 안내)" in md
