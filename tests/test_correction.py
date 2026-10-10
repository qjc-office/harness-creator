from hc.correction import is_correction, is_non_user

def test_real_corrections():
    for t in ("그렇게 하지 말라고 했잖아", "이건 틀렸어", "다시는 그 파일 지우지 마", "don't do that", "왜 이렇게 했어?"):
        assert is_correction(t), t

def test_not_corrections():
    for t in ("A가 아니라 B로 해줘", "이게 wrong인지 검토해줘", "테스트 돌려줘"):
        assert not is_correction(t), t

def test_non_user_payloads():
    assert is_non_user("<task-notification>...</task-notification>")
    assert is_non_user("너는 두 번째 모델로서 적대적 코드 리뷰를 한다")
    assert is_non_user("x" * 401)
    assert not is_non_user("그렇게 하지 마")

from hc.correction import is_resume

def test_zh_and_en_corrections():
    for t in ("不对，不要这样改", "你又错了吧！", "that's wrong, revert it"):   # is_correction 하한 5자(v1 그대로)를 넘는 표본
        assert is_correction(t), t

def test_resume_phrases():
    for t in ("continue", "Continue.", "이어서 진행해", "계속", "继续"):
        assert is_resume(t), t
    for t in ("continue the migration in api/", "이어서 테스트 코드도 써줘"):
        assert not is_resume(t), t

# --- zh/en 일상문 오탐, en/zh 접두가 실제 정정을 비사용자로 버리던 문제 ---
def test_zh_en_everyday_sentences_not_corrections():
    for t in ("没错了，就这样提交吧", "这个布局不对称", "为什么还需要这个依赖？",
              "please don't forget to run the tests", "I don't know why CI fails",
              "Do not push yet, open a PR first"):
        assert not is_correction(t), t

def test_zh_en_corrections_still_detected():
    for t in ("你又错了吧！", "不对，不要这样改", "为什么又改了？", "that's wrong, revert it",
              "don't do that", "No, don't do that again"):
        assert is_correction(t), t

def test_en_zh_you_are_corrections_are_user_messages():
    for t in ("You are wrong, revert it", "you are deleting the tests again, don't do that",
              "你现在是在乱改代码，不要这样"):
        assert not is_non_user(t), t
        assert is_correction(t), t

def test_system_prompt_prefixes_still_non_user():
    for t in ("You are a code reviewer. Find bugs.", "you are an assistant that summarizes",
              "你是一个代码审查助手", "너는 두 번째 모델로서 적대적 코드 리뷰를 한다", "이번엔 너는 리뷰어 역할이다"):
        assert is_non_user(t), t

# --- 영어 금지형 정정은 문장 시작이면 잡고, 같은 문장에 yet·until·worry·hesitate·forget·know가 있으면 뺀다 ---
def test_en_prohibitive_corrections():
    for t in ("don't touch the config", "Don't change the API", "No, don't use pnpm", "Don't modify tests",
              "don't delete the tests", "Don't use mocks", "Don't push to main", "no, do not commit",
              "please don't", "I said don't"):
        assert is_correction(t), t

def test_en_prohibitive_everyday_excluded():
    for t in ("Do not push yet, open a PR first", "don't merge it until CI passes", "don't worry about it",
              "Don't hesitate to ask again", "please don't forget to run the tests", "I don't know why CI fails"):
        assert not is_correction(t), t
