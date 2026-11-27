"""流式分句器。

LLM 的增量文本按句末标点切段后送 TTS；过短的片段先留在缓冲里，避免 TTS
为「好。」这类片段单独合成。`max_chars` 是硬上限，保证单段不会无限增长。
"""

from __future__ import annotations

_SENTENCE_ENDS = frozenset("。！？!?；;\n")
_TRAILING_CLOSERS = frozenset("'\"”’』」）)]】》")
_PREFERRED_BREAKS = frozenset("，,、；;：: \t")
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "st",
        "jr",
        "sr",
        "vs",
        "etc",
        "inc",
        "ltd",
        "co",
        "no",
        "fig",
        "al",
        "e.g",
        "i.e",
        "cf",
    }
)


class SentenceSegmenter:
    """有状态的增量分句；`feed()` 返回已经可以送 TTS 的完整片段。"""

    def __init__(self, min_chars: int = 4, max_chars: int = 120) -> None:
        if min_chars < 1:
            raise ValueError("min_chars must be >= 1")
        if max_chars <= min_chars:
            raise ValueError("max_chars must be > min_chars")
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        if delta:
            self._buffer += delta
        out: list[str] = []
        self._drain_sentences(out)
        self._drain_max_chars(out)
        return out

    def flush(self) -> list[str]:
        piece = self._buffer.strip()
        self._buffer = ""
        if self._is_meaningful(piece):
            return [piece]
        return []

    def _drain_sentences(self, out: list[str]) -> None:
        while True:
            cut = self._find_sentence_end()
            if cut is None:
                return
            piece = self._buffer[: cut + 1].strip()
            self._buffer = self._buffer[cut + 1 :]
            if self._is_meaningful(piece):
                out.append(piece)

    def _find_sentence_end(self) -> int | None:
        text = self._buffer
        for index, char in enumerate(text):
            if char not in _SENTENCE_ENDS:
                if char != "." or not self._is_period_end(index):
                    continue
            end = index
            while end + 1 < len(text) and (
                text[end + 1] in _SENTENCE_ENDS
                or text[end + 1] in _TRAILING_CLOSERS
            ):
                end += 1
            if len(text[: end + 1].strip()) < self.min_chars:
                continue
            return end
        return None

    def _is_period_end(self, index: int) -> bool:
        text = self._buffer
        next_char = text[index + 1] if index + 1 < len(text) else ""
        # 3.14、example.com 这类点不是句末。
        if next_char and not next_char.isspace():
            return False
        start = index
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "."):
            start -= 1
        return text[start:index].lower() not in _ABBREVIATIONS

    def _drain_max_chars(self, out: list[str]) -> None:
        while len(self._buffer) > self.max_chars:
            window = self._buffer[: self.max_chars]
            cut = max(window.rfind(char) for char in _PREFERRED_BREAKS)
            cut = cut + 1 if cut > 0 else self.max_chars
            piece = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:]
            if self._is_meaningful(piece):
                out.append(piece)

    @staticmethod
    def _is_meaningful(text: str) -> bool:
        return any(char.isalnum() for char in text)
