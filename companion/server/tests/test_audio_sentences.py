"""流式分句器：把 LLM 增量文本切成可送 TTS 的句子。"""

import pytest

from companion.server.app.audio.sentences import SentenceSegmenter


def test_splits_on_chinese_and_ascii_endings():
    seg = SentenceSegmenter(min_chars=2)
    assert seg.feed("你好。") == ["你好。"]
    assert seg.feed("Hello!") == ["Hello!"]
    assert seg.feed("为什么？") == ["为什么？"]
    assert seg.feed("第一；") == ["第一；"]
    assert seg.feed("line\n") == ["line"]


def test_short_sentences_are_merged_until_min_chars():
    seg = SentenceSegmenter(min_chars=4)
    assert seg.feed("好。") == []
    assert seg.feed("再说。") == ["好。再说。"]


def test_never_emits_one_char_segment():
    seg = SentenceSegmenter(min_chars=4)
    assert seg.feed("好。") == []
    assert seg.feed("世界。") == ["好。世界。"]


def test_cross_chunk_state_and_flush():
    seg = SentenceSegmenter(min_chars=4)
    assert seg.feed("你好") == []
    assert seg.feed("世界。再见") == ["你好世界。"]
    assert seg.flush() == ["再见"]
    assert seg.flush() == []


def test_flush_returns_remaining_text_only_once():
    seg = SentenceSegmenter(min_chars=4)
    assert seg.feed("一句话。") == ["一句话。"]
    assert seg.feed("尾巴") == []
    assert seg.flush() == ["尾巴"]
    assert seg.flush() == []


def test_max_chars_hard_cut_without_separator():
    seg = SentenceSegmenter(min_chars=2, max_chars=6)
    assert seg.feed("abcdefghij") == ["abcdef"]
    assert seg.flush() == ["ghij"]


def test_max_chars_prefers_a_separator():
    seg = SentenceSegmenter(min_chars=2, max_chars=12)
    assert seg.feed("你好世界，再见朋友，我们走吧") == ["你好世界，再见朋友，"]
    assert seg.flush() == ["我们走吧"]


def test_pure_punctuation_produces_nothing():
    seg = SentenceSegmenter(min_chars=1)
    assert seg.feed("。。。！！") == []
    assert seg.feed("\n\n") == []
    assert seg.flush() == []


def test_empty_input():
    seg = SentenceSegmenter()
    assert seg.feed("") == []
    assert seg.flush() == []


def test_does_not_split_common_abbreviations():
    seg = SentenceSegmenter(min_chars=1)
    assert seg.feed("Mr. Smith went home. ") == ["Mr. Smith went home."]


def test_does_not_split_decimal_points():
    seg = SentenceSegmenter(min_chars=1)
    assert seg.feed("Pi is 3.14. ") == ["Pi is 3.14."]


def test_consumes_trailing_closers():
    seg = SentenceSegmenter(min_chars=2)
    assert seg.feed("真的吗？！”然后") == ["真的吗？！”"]


def test_semicolon_and_newline_split():
    seg = SentenceSegmenter(min_chars=2)
    assert seg.feed("第一句；第二句\n") == ["第一句；", "第二句"]


@pytest.mark.parametrize(
    "min_chars,max_chars",
    [(0, 20), (-1, 20), (10, 10), (10, 5)],
)
def test_rejects_invalid_limits(min_chars, max_chars):
    with pytest.raises(ValueError):
        SentenceSegmenter(min_chars=min_chars, max_chars=max_chars)
