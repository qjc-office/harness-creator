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
