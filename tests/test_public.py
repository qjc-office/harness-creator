import pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.ko.md", "README.zh.md")

def test_repo_has_no_forbidden_tokens():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_public.py")], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr

def test_checker_detects_hashed_token(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import check_public as cp
    h = cp.digest("forbiddenword")
    assert cp.scan_text("a forbiddenword b\nok", {h}) == [1]
    assert cp.scan_text("FORBIDDENWORD", {h}) == [1]
    assert cp.scan_text("forbiddenwordx", {h}) == []

def test_untracked_files_are_scanned():
    sys.path.insert(0, str(ROOT / "tools"))
    import check_public as cp
    probe = ROOT / "untracked-probe.txt"
    probe.write_text("x\n")
    try:
        assert probe in cp.files()          # 커밋·스테이징 전 파일도 검사 대상
    finally:
        probe.unlink()

def test_gitignored_files_are_not_scanned():
    """.gitignore로 뺀 로컬 전용 파일은 공개되지 않으므로 검사 대상이 아니다(P24)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import check_public as cp
    probe = ROOT / ".pytest_cache" / "ignored-probe.txt"
    probe.parent.mkdir(exist_ok=True)
    probe.write_text("x\n")
    try:
        assert probe not in cp.files()
    finally:
        probe.unlink()

def test_hash_mode_prints_digests():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_public.py"), "--hash", "Abc", "def"],
                       capture_output=True, text=True)
    sys.path.insert(0, str(ROOT / "tools"))
    import check_public as cp
    assert r.returncode == 0 and r.stdout.split() == [cp.digest("abc"), cp.digest("def")]

def test_forbidden_list_holds_hashes_only():
    import re
    lines = [l for l in (ROOT / "tools" / "forbidden.sha256").read_text().splitlines() if l and not l.startswith("#")]
    assert lines and all(re.fullmatch(r"[0-9a-f]{64}", l) for l in lines)

def test_readmes_and_license_exist():
    for n in ("README.md", "README.ko.md", "README.zh.md", "LICENSE"):
        assert (ROOT / n).is_file(), n
    assert "MIT License" in (ROOT / "LICENSE").read_text()

def test_readmes_state_limits():
    """원장 이월: 가림 한계·forget 범위·timeout_s·--restricted·감지 정확도·frontmatter 재생성을 3개 언어 모두 적는다."""
    for n in READMES:
        t = (ROOT / n).read_text(encoding="utf-8")
        for must in ("user:pw@host", "password: |", "-p<", "<password>", "Authorization: Basic",
                     "redact.lexicon_files", "redact.pattern_files",
                     "cursor.json", "collect-stats.json", "events", "signals", "reports", "logs",
                     "config.json", "decisions.jsonl", "applied.jsonl", "backups", "disabled",
                     "judge.timeout_s", "null", "--restricted", "frontmatter"):
            assert must in t, (n, must)
        assert "/Users/" not in t, n
