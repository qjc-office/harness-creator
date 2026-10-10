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


# --- fix round 1 ---
import json
import pytest

_GHP = "gh" + "p_" + "Qw7" * 6   # 가짜 토큰은 문자열 결합으로

def _broken_config(cache, kind):
    from hc.config import save_user_config
    if kind == "bad_json":
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "config.json").write_text("{", encoding="utf-8")
    elif kind == "floor_str":
        save_user_config({"redact": {"lexicon_min_terms": "abc"}})
    elif kind == "redact_str":
        save_user_config({"redact": "x"})
    elif kind == "ratio_str":
        save_user_config({"unknown_type_warn_ratio": "x"})

KINDS = ["bad_json", "floor_str", "redact_str", "ratio_str"]

@pytest.mark.parametrize("kind", KINDS)
def test_broken_config_error_path_still_writes_first_line(cache, kind):
    _broken_config(cache, kind)
    md = render(None, {**ST, "lexicon_terms": 3}, 0, "rc=3", lang="ko", error_kind="collect")
    assert md.splitlines()[0] == "# harness-creator 리포트: 실패"
    assert "수집 실패 rc=3" in md

@pytest.mark.parametrize("kind", KINDS)
def test_failed_report_keeps_stats_warnings_skips_config_ones(cache, kind):
    """실패 리포트도 통계만으로 계산되는 경고는 낸다. 설정에 기대는 경고(하한·모르는 타입 비율)는 건너뛴다."""
    _broken_config(cache, kind)
    st = {**ST, "lexicon": False, "errors": 1, "malformed": 2, "unknown": 50}
    md = render(None, st, 0, "rc=3", lang="ko", error_kind="collect")
    assert md.splitlines()[0] == "# harness-creator 리포트: 실패"
    assert "> 추가 가림 목록 없음." in md and "> 경고: 파싱 실패 파일 1개." in md and "깨진 레코드 2줄" in md
    assert "하한" not in md and "모르는 레코드 타입 비율" not in md
    md2 = render(None, {**ST, "errors": "x"}, 0, "rc=3", lang="ko", error_kind="collect")
    assert "수집 통계 손상" in md2

def test_failed_report_skips_floor_even_with_valid_config(cache):
    from hc.config import save_user_config
    save_user_config({"redact": {"lexicon_min_terms": 10}})
    md = render(None, {**ST, "lexicon_terms": 3}, 0, "rc=3", lang="ko", error_kind="collect")
    assert "하한" not in md

@pytest.mark.parametrize("kind", KINDS)
def test_broken_config_normal_path_uses_defaults(cache, kind):
    _broken_config(cache, kind)
    md = render({"candidates": [C]}, {**ST, "lexicon_terms": 3}, 1, None, lang="ko")
    assert md.splitlines()[0] == "# harness-creator 리포트: 후보 1건"
    assert "하한" not in md
    # 비율도 기본값(0.05)으로 판정한다
    st = {**ST, "unknown": 9}
    assert incomplete_reasons(st) == ["모르는 레코드 비율 9.0%"]
    assert render({"candidates": []}, st, 0, None, lang="ko").splitlines()[0] == \
        "# harness-creator 리포트: 후보 없음 (불완전: 모르는 레코드 비율 9.0%)"

@pytest.mark.parametrize("terms", [0, None])
def test_floor_warning_when_lexicon_empty(cache, terms):
    from hc.config import save_user_config
    save_user_config({"redact": {"lexicon_min_terms": 10}})
    md = render({"candidates": []}, {**ST, "lexicon": False, "lexicon_terms": terms}, 0, None, lang="ko")
    head = md.split("\n## ")[0]
    assert "> 추가 가림 목록 없음." in head
    assert "> 경고: 추가 가림 목록 0건(하한 10)." in head

def _leaky():
    return {**C, "title": "t " + _GHP, "expected_effect": "e " + _GHP, "overlaps": ["o " + _GHP],
            "evidence": {"sessions": 2, "date_range": "2026-10-01..2026-10-07", "quotes": ["q " + _GHP]},
            "proposed_change": {"action": "create", "path": "rules/a.md", "summary": "s " + _GHP}}

def test_model_strings_redacted(cache):
    gates = {"codex_notes": [{**_leaky(), "id": "hc-2026-10-09-02"}]}
    md = render({"candidates": [_leaky()]}, ST, 1, None, lang="ko", gates=gates)
    assert "Qw7Qw7" not in md and "[SECRET]" in md
    assert "rules/a.md" in md and "hc-2026-10-09-02" in md

def test_redactor_build_failure_falls_back_to_builtin(cache, tmp_path):
    from hc.config import save_user_config
    save_user_config({"redact": {"lexicon_files": [str(tmp_path / "missing.txt")]}})
    md = render({"candidates": [_leaky()]}, ST, 1, None, lang="ko")
    assert md.splitlines()[0] == "# harness-creator 리포트: 후보 1건"
    assert "Qw7Qw7" not in md and "[SECRET]" in md

@pytest.mark.parametrize("v", ["4", True, -1, 2.5])
def test_resume_skipped_non_int_ignored(v):
    md = render({"candidates": []}, ST, 0, None, lang="ko", gates={"resume_skipped": v})
    assert "## 걸러진 신호" not in md

def test_unknown_lang_falls_back_to_en():
    assert render({"candidates": []}, ST, 0, None, lang="fr").splitlines()[0] == "# harness-creator report: no candidates"
    assert incomplete_reasons({**ST, "errors": 2}, lang="xx") == ["parse errors 2"]


# --- fix round 2 ---
def test_effects_and_error_detail_redacted(cache):
    effects = [{"candidate_id": "x " + _GHP, "recurred": True, "last_seen": "2026-10-08T00:00:00Z"}]
    md = render({"candidates": []}, ST, 0, None, lang="ko", effects=effects)
    assert "Qw7Qw7" not in md and "[SECRET]" in md and "2026-10-08T00:00:00Z" in md
    md = render(None, ST, 0, "rc=3 " + _GHP, lang="ko", error_kind="judge", effects=effects)
    assert md.splitlines()[0] == "# harness-creator 리포트: 실패"
    assert "Qw7Qw7" not in md and "rc=3" in md
    md = render(None, ST, 0, "boom " + _GHP, lang="ko")   # error_kind 없는 실패
    assert "Qw7Qw7" not in md and "boom" in md
