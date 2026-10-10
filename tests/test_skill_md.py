import pathlib, re
ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "harness-creator" / "SKILL.md"

def test_frontmatter_and_contract():
    t = SKILL.read_text(encoding="utf-8")
    fm = re.match(r"^---\n(.*?)\n---", t, re.S).group(1)
    assert "name: harness-creator" in fm and "description:" in fm
    for must in ('scripts/cli.py" status', "consent-text", "consent --accept", "run-analysis.sh", "run_in_background",
                 "AskUserQuestion", "multiSelect", "decide", "apply-plan", "apply-write", "undo", "forget", "where",
                 "generated_by: harness-creator", "candidate_id", "global_rule"):
        assert must in t, must
    assert "—" not in t

def test_skill_never_gives_judge_tools():
    t = SKILL.read_text(encoding="utf-8")
    assert "dangerously-skip-permissions" not in t and "~/.codex" in t

def test_skill_surfaces_risks_before_write():
    """원장 이월: 승인 질문에 정적 검사 경고·극광역 glob·이름 불일치를 넣는 것은 SKILL 몫이다(spec §7-3)."""
    t = SKILL.read_text(encoding="utf-8")
    # 경고는 승인 질문 안에 들어가야 한다
    step = t.split("Run `apply-plan", 1)[1].split("## 6.", 1)[0]
    assert "warnings" in step and "in the question" in step
    for must in ("**/*.*", "@(**)", "folder name", "--force", "frontmatter is regenerated"):
        assert must in t, must
    # 쓰기는 사용자 yes 뒤에만
    assert step.index("AskUserQuestion") < step.index("apply-write")
