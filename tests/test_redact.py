import json
import pytest
from hc.redact import Redactor, RedactError, luhn_ok, cn_id_ok, from_config

# 가짜 값은 문자열 결합으로 만든다(소스에 원문 토큰을 두지 않는다)
_SK = "sk" + "-ant-api03-" + "Ab1" * 8
_GHP = "gh" + "p_" + "Qw7" * 6
_HEX = "0123456789abcdef" * 3

def R(**kw):
    return Redactor(**kw)

@pytest.mark.parametrize("raw,leak", [
    ("key " + _SK, "Ab1Ab1"),
    ("t " + _GHP, "Qw7Qw7"),
    ("aws " + "AK" + "IA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
    ("slack " + "xo" + "xb-" + "1234567890-abcdef", "1234567890-abcdef"),
    ("google " + "AI" + "za" + "SyD" + "x" * 32, "SyD" + "x" * 32),
    ("stripe " + "sk" + "_live_" + "Ab3" * 6, "Ab3" * 6),
    ("jwt " + "ey" + "J" + "a" * 24 + "." + "b" * 12 + "." + "c" * 12, "b" * 12),
], ids=["anthropic", "github", "aws", "slack", "google", "stripe", "jwt"])
def test_builtin_secrets(raw, leak):
    out = R().redact(raw)
    assert leak not in out and "[SECRET]" in out

def test_pem_block():
    body = "MIIEv" + "QIBADANBgkqhkiG9w0BAQEFAASC" * 2
    pem = "-----BEGIN " + "PRIVATE KEY-----\n" + body + "\n-----END " + "PRIVATE KEY-----"
    assert R().redact("a\n" + pem + "\nb") == "a\n[SECRET]\nb"

def test_assignment_and_bearer():
    assert R().redact("export CRON" + "_SECRET=" + _HEX) == "export CRON_SECRET=[SECRET]"
    assert R().redact('api_key: "' + _HEX + '"') == 'api_key: "[SECRET]"'
    assert R().redact("키CRON" + "_SECRET=" + "0123456789abcdef") == "키CRON_SECRET=[SECRET]"
    tok = "abc." + _HEX + "-xyz"
    out = R().redact("Authorization: Bear" + "er " + tok)
    assert tok not in out and "Bearer [SECRET]" in out

@pytest.mark.parametrize("raw", ["task-abcdefghijklmnop", "/x/task-0123456789abcd/y", "<task-notification>",
                                 "x" + _GHP])
def test_prefix_inside_word_unchanged(raw):
    assert R().redact(raw) == raw

@pytest.mark.parametrize("raw", ["key_" + _SK, "x-" + _SK, "--token-" + _GHP])
def test_prefix_after_underscore_or_dash_masked(raw):
    assert "[SECRET]" in R().redact(raw)

@pytest.mark.parametrize("raw,leak", [
    ("mail a.b+c@example.co.kr now", "a.b+c@example.co.kr"),
    ("kr 010-1234-5678", "010-1234-5678"),
    ("kr +82 10 1234 5678", "1234 5678"),
    ("cn 13812345678", "13812345678"),
    ("cn +86 138 1234 5678", "138 1234 5678"),
    ("us (415) 555-2671", "555-2671"),
    ("us +1 415-555-2671", "415-555-2671"),
])
def test_contacts(raw, leak):
    assert leak not in R().redact(raw)

@pytest.mark.parametrize("raw", ["2026-10-09 12:30", "version 2.100.300", "pid 1728000000", "port 8080"])
def test_numbers_not_contacts(raw):
    assert R().redact(raw) == raw

def test_card_luhn_only():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert "[CARD]" in R().redact("card 4111 1111 1111 1111")
    assert R().redact("num 4111 1111 1111 1112") == "num 4111 1111 1111 1112"

def test_kr_rrn():
    RRN = "900101" + "-" + "1234567"   # 합성 예시 번호, 원문을 소스에 두지 않는다
    assert R().redact("주민 " + RRN + " 확인") == "주민 [ID] 확인"
    BAD = "991399" + "-" + "1234567"
    assert R().redact("코드 " + BAD) == "코드 " + BAD       # 13월은 날짜가 아니다

def test_cn_id_checksum():
    good = "11010519491231002X"
    assert cn_id_ok(good) and not cn_id_ok("110105194912310021")
    assert R().redact("身份证 " + good) == "身份证 [ID]"
    assert R().redact("n 110105194912310021") == "n 110105194912310021"

@pytest.mark.parametrize("raw,want", [
    ("/Users/alice/proj/a.py", "~/proj/a.py"),
    ("/home/bob/.claude", "~/.claude"),
    ("C:\\Users\\carol\\x", "~\\x"),
    ("/Users/Shared/x", "/Users/Shared/x"),
])
def test_home_paths(raw, want):
    assert R().redact(raw) == want

def test_lexicon_slot(tmp_path):
    lex = tmp_path / "lex.json"
    lex.write_text(json.dumps({"orgs": ["Acme", "가나다" + "상사"], "people": ["홍길순"]}), encoding="utf-8")
    r = R(lexicon_files=[str(lex)])
    assert r.has_lexicon and r.lexicon_terms == 3
    assert r.redact("acme-ax ACME Acme") == "[NAME]-ax [NAME] [NAME]"
    assert r.redact("acmecorp") == "acmecorp"                     # 영문은 단어 경계
    assert r.redact("가나다" + "상사와 홍길순") == "[NAME]와 [NAME]"     # 한글은 조사가 붙으므로 부분 일치

def test_lexicon_list_format_and_label(tmp_path):
    lex = tmp_path / "l.json"; lex.write_text(json.dumps(["Zeta"]))
    assert R(lexicon_files=[str(lex)], lexicon_label="L3").redact("zeta") == "[L3]"

def test_no_lexicon():
    r = R()
    assert r.has_lexicon is False and r.lexicon_terms == 0

def test_pattern_slot_whole_and_replace(tmp_path):
    pf = tmp_path / "p.json"
    pf.write_text(json.dumps({"patterns": [
        {"label": "L4", "regex": "(?<![a-z])(?:salary)(?![a-z])|급여", "flags": "i", "whole": True},
        {"label": "MONEY", "regex": r"\d[\d,]*만원", "flags": "", "whole": False}]}), encoding="utf-8")
    r = R(pattern_files=[str(pf)])
    assert r.redact("이번 달 급여 계산") == "[L4]"
    assert r.redact("SALARY 표") == "[L4]"
    assert r.redact("versus") == "versus"
    assert r.redact("견적 3,200만원") == "견적 [MONEY]"

@pytest.mark.parametrize("content", [None, "{broken", '{"patterns": []}', '{"patterns": [{"label": "x", "regex": "("}]}',
                                     '{"patterns": [{"label": "L4", "regex": "a", "whole": "yes"}]}'])
def test_pattern_slot_fail_closed(tmp_path, content):
    pf = tmp_path / "p.json"
    if content is not None:
        pf.write_text(content)
    with pytest.raises(RedactError):
        R(pattern_files=[str(pf)])

@pytest.mark.parametrize("content", [None, "{broken", '"str"', '[1, 2]',
                                     '{"orgs": ["Acme"], "x": "ignored"}'])
def test_lexicon_slot_fail_closed(tmp_path, content):
    lf = tmp_path / "l.json"
    if content is not None:
        lf.write_text(content)
    with pytest.raises(RedactError):
        R(lexicon_files=[str(lf)])

def test_from_config_tilde(tmp_path, monkeypatch):
    home = tmp_path / "h"; (home / "c").mkdir(parents=True)
    (home / "c" / "l.json").write_text(json.dumps(["Omega"]))
    monkeypatch.setenv("HOME", str(home))
    r = from_config({"redact": {"lexicon_files": ["~/c/l.json"], "pattern_files": [], "lexicon_label": "NAME"}})
    assert r.redact("omega") == "[NAME]"

def test_clean_text_unchanged():
    assert R().redact("pytest가 실패해요") == "pytest가 실패해요"
    assert R().redact("") == ""

def test_assignment_json_quoted_key():
    # JSON·dict 형태는 키 이름 뒤에 따옴표가 붙는다. 세션 기록의 도구 입력이 주로 이 형태다.
    assert R().redact('{"CRON' + '_SECRET": "' + _HEX + '"}') == '{"CRON_SECRET": "[SECRET]"}'
    assert R().redact("{'pass" + "word': '" + _HEX + "'}") == "{'password': '[SECRET]'}"

def test_lexicon_mixed_script_case_insensitive(tmp_path):
    lex = tmp_path / "m.json"; lex.write_text(json.dumps(["Acme" + "상사"]), encoding="utf-8")
    assert R(lexicon_files=[str(lex)]).redact("ACME" + "상사 acme" + "상사") == "[NAME] [NAME]"


# --- fix round 1 ---
import time, base64

def test_assignment_quoted_value_with_spaces():
    pw = "correct" + " horse battery staple"
    out = R().redact("export PASS" + "WORD='" + pw + "'")
    assert out == "export PASSWORD='[SECRET]'"
    v = "my sec" + "ret value 1234 56"
    out = R().redact('{"pass' + 'word": "' + v + '"}')
    assert out == '{"password": "[SECRET]"}'

def test_assignment_escaped_json():
    v = "my sec" + "ret value 1234 56"
    raw = '{\\"pass' + 'word\\": \\"' + v + '\\"}'
    out = R().redact(raw)
    assert v not in out and "sec" + "ret value" not in out
    assert out == '{\\"password\\": \\"[SECRET]\\"}'

def test_assignment_unquoted_keeps_structure():
    assert R().redact("{TOK" + "EN=" + _HEX + "}") == "{TOKEN=[SECRET]}"
    assert R().redact("a?api" + "_key=" + _HEX + "&next=1") == "a?api_key=[SECRET]&next=1"
    assert R().redact("f(SEC" + "RET=" + _HEX + ", y)") == "f(SECRET=[SECRET], y)"
    assert R().redact("TOK" + "EN=" + _HEX + ";ls") == "TOKEN=[SECRET];ls"

def test_assignment_short_quoted_unchanged():
    assert R().redact('{"key": "Enter"}') == '{"key": "Enter"}'

@pytest.mark.parametrize("sep", ["", " "])
def test_kr_rrn_without_hyphen(sep):
    rrn = "900101" + sep + "1234567"
    assert R().redact("주민 " + rrn + " 확인") == "주민 [ID] 확인"

def test_kr_rrn_inside_longer_digits_unchanged():
    raw = "n 1" + "9001011234567" + "9"
    assert "[ID]" not in R().redact(raw)

@pytest.mark.parametrize("raw", ["a" * 40000, base64.urlsafe_b64encode(bytes(range(256)) * 240).decode()[:80000]],
                         ids=["a40k", "b64url80k"])
def test_email_regex_linear_time(raw):
    t = time.monotonic()
    R().redact(raw)
    assert time.monotonic() - t < 1.0

def test_lexicon_label_literal(tmp_path):
    lex = tmp_path / "l.json"; lex.write_text(json.dumps(["Zeta"]))
    assert R(lexicon_files=[str(lex)], lexicon_label="A\\1").redact("zeta") == "[A\\1]"

@pytest.mark.parametrize("raw,leak", [
    ("aws " + "AS" + "IA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
    ("slack " + "xa" + "pp-" + "1-A0123-456789-abcdef", "456789-abcdef"),
    ("slack " + "xo" + "xe-" + "1-abcdefghij0123", "abcdefghij0123"),
])
def test_more_prefixes(raw, leak):
    out = R().redact(raw)
    assert leak not in out and "[SECRET]" in out
