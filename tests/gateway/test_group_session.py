from gateway.platforms.group_session import SLEEP_KEYWORDS, is_sleep_command


def test_exact_sleep_keyword_is_a_command():
    assert is_sleep_command("sleep") is True
    assert is_sleep_command("stop") is True
    assert is_sleep_command("go to sleep") is True


def test_case_and_whitespace_are_normalized():
    assert is_sleep_command("  SLEEP  ") is True
    assert is_sleep_command("Go   To   Sleep") is True
    assert is_sleep_command("Stop Listening") is True


def test_sleep_word_inside_a_sentence_is_not_a_command():
    assert is_sleep_command("i need to sleep now please") is False
    assert is_sleep_command("are you going to sleep") is False
    assert is_sleep_command("stop being annoying") is False


def test_empty_text_is_not_a_command():
    assert is_sleep_command("") is False
    assert is_sleep_command("   ") is False


def test_sleep_keywords_set_is_non_empty():
    assert "sleep" in SLEEP_KEYWORDS
