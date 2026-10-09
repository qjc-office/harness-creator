from hc.lang import LANGS, load_pack, detect_lang, script_of

def test_packs_have_same_keys():
    keys = [(set(load_pack(c)), set(load_pack(c)["report"]), set(load_pack(c)["consent"])) for c in LANGS]
    assert all(k == keys[0] for k in keys)

def test_placeholders_match_across_packs():
    import re
    ph = lambda s: sorted(re.findall(r"\{[a-z_]*\}", s))
    for sec in ("report", "consent"):
        for k, v in load_pack("en")[sec].items():
            for c in ("ko", "zh"):
                assert ph(load_pack(c)[sec][k]) == ph(v), (c, sec, k)

def test_script_of():
    assert script_of("테스트 돌려줘") == "ko"
    assert script_of("请继续修复这个问题") == "zh"
    assert script_of("please fix the tests") == "en"
    assert script_of("1234 !!") == ""

def test_detect_lang_majority_and_default():
    assert detect_lang(["테스트", "고쳐", "fix it"]) == "ko"
    assert detect_lang(["不对", "错了", "ok"]) == "zh"
    assert detect_lang([]) == "en"
