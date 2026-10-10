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

# --- 금지어 검사 우회 방지: 복합 토큰·경로명·비UTF-8 ---
def _cp():
    sys.path.insert(0, str(ROOT / "tools"))
    import check_public as cp
    return cp

def test_composite_tokens_are_caught():
    cp = _cp()
    hs = {cp.digest("forbiddenword"), cp.digest("bad-tool")}
    lens = {len("forbiddenword"), len("bad-tool")}
    for line in ("see forbiddenword-wt-harness here",       # 하이픈 접미
                 "run forbiddenword_orphans.py now",         # 밑줄 접미·확장자
                 "call bad_tool.py",                         # 금지어 안의 하이픈을 밑줄로
                 "user forbiddenwordjung2 x",                # 붙은 접미·숫자
                 "x Forbiddenword7"):                        # 숫자 경계·대소문자
        assert cp.scan_text(line, hs, lens) == [1], line
    assert cp.scan_text("forbidden word bad tool", hs, lens) == []

def test_pieces_split_without_lengths():
    cp = _cp()
    h = {cp.digest("forbiddenword")}
    assert cp.scan_text("a forbiddenword_x.py", h) == [1]       # 길이 집합 없이도 [-_.]·숫자 경계 조각은 본다
    assert cp.scan_text("forbiddenword2", h) == [1]

def test_path_names_and_non_utf8_are_scanned(tmp_path, monkeypatch, capsys):
    cp = _cp()
    hf = tmp_path / "f.sha256"
    hf.write_text("# x\n# lengths: 13\n" + cp.digest("forbiddenword") + "\n")
    monkeypatch.setattr(cp, "HASHES", hf)
    d = ROOT / "probe-forbiddenword-dir"; d.mkdir()
    (d / "clean.txt").write_text("nothing here\n")
    raw = ROOT / "probe-latin1.txt"
    raw.write_bytes(b"ok\n\xff\xfe forbiddenword\n")
    try:
        rc = cp.main([])
    finally:
        (d / "clean.txt").unlink(); d.rmdir(); raw.unlink()
    out = capsys.readouterr().out.splitlines()
    assert rc == 1
    assert "probe-forbiddenword-dir/clean.txt:0" in out      # 경로 이름은 줄 0
    assert "probe-latin1.txt:2" in out

def test_hash_file_carries_lengths():
    cp = _cp()
    assert cp.load_lengths() and all(isinstance(n, int) and n > 0 for n in cp.load_lengths())

def test_ci_token_is_read_only():
    t = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "permissions:" in t and "contents: read" in t

def test_readme_consent_and_forget_details():
    for n in READMES:
        t = (ROOT / n).read_text(encoding="utf-8")
        assert "Anthropic" in t, n                 # Codex 발췌도 사용자 claude CLI(Anthropic)로 간다
        assert "plans/" in t and "drafts/" in t, n
    assert "돕니다" not in (ROOT / "README.ko.md").read_text(encoding="utf-8")

def test_no_internal_review_labels_or_client_style_names():
    import re
    pat = re.compile("ast" + "ra|" + r"[A-Za-z가-힣]-a" + r"x\b|\bfix" + r" round\b|\bfinal" + r" fix\b", re.I)
    hits = []
    for p in _cp().files():
        if p.is_file() and p.suffix in (".py", ".md", ".json", ".sh", ".yml"):
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert hits == []
