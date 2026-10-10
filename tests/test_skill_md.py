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

def test_skill_treats_content_as_data_and_records_no():
    t = SKILL.read_text(encoding="utf-8")
    never = t.split("## Never", 1)[1]
    assert "data, not instructions" in never and "Ignore" in never
    step = t.split("Run `apply-plan", 1)[1].split("## 6.", 1)[0]
    no = step[step.index("On no"):]
    assert "decide <id> reject --reason" in no.split("\n")[0]

def test_commands_use_plugin_root_directly():
    # 셸 변수는 다음 Bash 호출로 이어지지 않는다. 모든 명령이 경로를 직접 쓴다
    t = SKILL.read_text(encoding="utf-8")
    assert "HC=" not in t and "$HC" not in t
    assert 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cli.py" status' in t
    assert 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-analysis.sh"' in t
    for sub in ("undo <id>", "forget", "where"):
        assert f'python3 "${{CLAUDE_PLUGIN_ROOT}}/scripts/cli.py" {sub}' in t, sub
