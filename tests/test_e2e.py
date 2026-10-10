"""빈 HOME·데이터 폴더에서 합성 Claude·Codex 세션으로 동의 → 분석 → 리포트까지. 판정은 스텁."""
import json, os, pathlib, subprocess, sys, time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "cli.py"
SK = "sk" + "-ant-api03-" + "Zq9" * 8
CARD = "4111 1111 " + "1111 1111"
RRN = "900101" + "-1234567"
CNID = "11010519491231002X"
LEAKS = (SK, CARD, RRN, CNID, "alice@example.com", "010-2222-3333", "/Users/alice")
# 실패 서명은 숫자를 <N>으로 정규화하므로 신호 구분은 숫자가 아니라 단어로 한다.
ERRS = ("alpha", "bravo", "charlie", "delta", "echo")
# 첫 발화는 세션마다 달라야 반복 지시(3-gram Jaccard 0.6)로 묶이지 않는다. 가린 뒤 최대 Jaccard 0.23.
TOPICS = ("rename the billing module", "draft release notes for mobile", "profile the slow search query",
          "translate onboarding emails", "audit docker image sizes", "sketch a caching layer",
          "tidy the vendor folder", "plan the schema upgrade", "summarize yesterday's incidents",
          "benchmark json parsing")

STUB = r'''#!/usr/bin/env python3
import json, sys
p = sys.stdin.read()
data = json.loads(p.rsplit("INPUT JSON:", 1)[1])   # 프롬프트 1번 줄에도 이 표지가 있으므로 마지막 것에서 자른다
today = p.split("hc-", 1)[1][:10]
out = []
for i, s in enumerate(data["signals"][:6], 1):
    out.append({"id": f"hc-{today}-{i:02d}", "signal_ids": [s["id"]], "form": "rule", "title": f"t{i}",
                "evidence": {"sessions": len(s["sessions"]), "date_range": f"{s['first_seen'][:10]}..{s['last_seen'][:10]}",
                             "quotes": s["samples"][:1]},
                "overlaps": [], "proposed_change": {"action": "create", "path": f"rules/e2e-{i}.md", "summary": "s",
                                                    "paths": ["src/**"]},
                "expected_effect": "e"})
print(json.dumps({"candidates": out}))
'''

def _ts(base, minutes):
    return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def _claude_session(proj, sid, base, err, first):
    recs = [{"type": "user", "sessionId": sid, "timestamp": _ts(base, 0), "cwd": "/Users/alice/proj",
             "message": {"content": f"{first} contact alice@example.com 010-2222-3333 key {SK}"}}]
    for k in range(3):
        tid = f"{sid}-t{k}"
        recs.append({"type": "assistant", "sessionId": sid, "timestamp": _ts(base, 1 + k), "cwd": "/w",
                     "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "make"}}]}})
        recs.append({"type": "user", "sessionId": sid, "timestamp": _ts(base, 1 + k), "cwd": "/w",
                     "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": True,
                                              "content": f"{err} card {CARD} id {RRN} cn {CNID}"}]}})
    (proj / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))

def test_clean_room_end_to_end(tmp_path):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "p"; proj.mkdir(parents=True)
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for e in range(5):                      # 신호 5개(실패 루프), 각각 2세션 × 3회. 반복 지시·정정 신호는 0개
        for s in range(2):
            _claude_session(proj, f"e{e}s{s}", base + timedelta(hours=e * 3 + s), f"error kind {ERRS[e]}",
                            TOPICS[e * 2 + s])
    cx = home / ".codex" / "sessions" / "2026" / "10" / "09"; cx.mkdir(parents=True)
    (cx / "rollout-a.jsonl").write_text(json.dumps({"type": "session_meta", "timestamp": _ts(base, 0),
                                                     "payload": {"session_id": "cxa", "cwd": "/w"}}) + "\n")
    stub = tmp_path / "stub.py"; stub.write_text(STUB); stub.chmod(0o755)
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "en_US.UTF-8",
           "HC_CLAUDE_STUB": str(stub)}
    run = lambda *a: subprocess.run([sys.executable, str(CLI), *a], capture_output=True, text=True, env=env)
    assert run("consent", "--accept", "--codex", "yes").returncode == 0
    r = subprocess.run(["/bin/bash", str(ROOT / "scripts" / "run-analysis.sh")], capture_output=True, text=True,
                       env=env, timeout=300)
    data = home / ".claude" / "harness-creator"
    assert r.returncode == 0, r.stderr + (data / "logs" / "run.log").read_text()
    today = time.strftime("%Y-%m-%d")
    cands = json.loads((data / "reports" / f"{today}.candidates.json").read_text())["candidates"]
    assert len(cands) == 3
    gates = json.loads((data / "reports" / f"{today}.gates.json").read_text())
    assert sum(1 for f in gates["filtered"] if f["gate"] == "G5") == 2
    report = (data / "reports" / f"{today}.md").read_text()
    assert report.splitlines()[0] == "# harness-creator report: 3 candidate(s)"
    for p in data.rglob("*"):
        if p.is_file():
            body = p.read_text(encoding="utf-8", errors="replace")
            for leak in LEAKS:
                assert leak not in body, (p, leak)
