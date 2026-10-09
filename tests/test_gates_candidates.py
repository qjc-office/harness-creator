from hc.config import load_config
from hc.gates import gate_candidates

CFG = load_config()

def C(i, form="rule", sids=("s1",), paths=("src/**",), action="create", path=None):
    pc = {"action": action, "path": path or f"rules/r{i}.md", "summary": "s"}
    if paths:
        pc["paths"] = list(paths)
    return {"id": f"hc-2026-10-09-0{i}", "signal_ids": list(sids), "form": form, "title": f"t{i}", "proposed_change": pc}

def S(sid, surfaces=("claude",), n=4, days=("2026-10-07", "2026-10-08"), count=5):
    return {"id": sid, "surfaces": list(surfaces), "sessions": [f"x{i}" for i in range(n)],
            "groups": [f"x{i}" for i in range(n)], "days": list(days), "count": count}

def test_g3_codex_only_goes_to_notes():
    keep, codex, filt = gate_candidates([C(1, sids=("cx",))], [S("cx", surfaces=("codex",))], CFG)
    assert keep == [] and codex[0]["id"] == "hc-2026-10-09-01" and filt == [{"gate": "G3", "candidate_id": "hc-2026-10-09-01"}]

def test_mixed_surface_kept():
    keep, codex, _ = gate_candidates([C(1, sids=("m",))], [S("m", surfaces=("claude", "codex"))], CFG)
    assert len(keep) == 1 and codex == []

def test_g4_global_rule_needs_10_sessions_3_days():
    small = gate_candidates([C(1, paths=())], [S("s1", n=4)], CFG)
    assert small[0] == [] and small[2] == [{"gate": "G4", "candidate_id": "hc-2026-10-09-01"}]
    big = gate_candidates([C(1, paths=())], [S("s1", n=10, days=("2026-10-05", "2026-10-06", "2026-10-07"))], CFG)
    assert big[0][0]["global_rule"] is True

def test_g4_modify_reads_target_frontmatter(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "scoped.md").write_text("---\nname: x\npaths:\n  - \"a/**\"\n---\n")
    (tmp_path / "rules" / "global.md").write_text("---\nname: y\n---\n")
    ok = gate_candidates([C(1, paths=(), action="modify", path="rules/scoped.md")], [S("s1")], CFG, tmp_path)
    no = gate_candidates([C(1, paths=(), action="modify", path="rules/global.md")], [S("s1")], CFG, tmp_path)
    assert len(ok[0]) == 1 and no[0] == []

def test_g5_keeps_top3_by_count_times_sessions():
    sigs = [S(f"s{i}", n=i + 2, count=i + 1) for i in range(5)]
    cands = [C(i, sids=(f"s{i}",)) for i in range(5)]
    keep, _, filt = gate_candidates(cands, sigs, CFG)
    assert [c["id"] for c in keep] == ["hc-2026-10-09-04", "hc-2026-10-09-03", "hc-2026-10-09-02"]
    assert sorted(f["candidate_id"] for f in filt) == ["hc-2026-10-09-00", "hc-2026-10-09-01"]
    assert all(f["gate"] == "G5" for f in filt)

def test_non_rule_forms_skip_g4():
    keep, _, _ = gate_candidates([C(1, form="skill", paths=(), path="skills/x/SKILL.md")], [S("s1")], CFG)
    assert len(keep) == 1
