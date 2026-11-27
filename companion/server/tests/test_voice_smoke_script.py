"""烟测脚本的离线部分：Ogg 页计数与缺 Key 拒绝（不联网）。"""

from __future__ import annotations

from companion.scripts.voice_dashscope_smoke import (
    _count_ogg_pages,
    _OggPageCounter,
    _run_tts,
    main,
)
from companion.server.app.audio.ogg import OggOpusMuxer
from companion.server.app.config import settings


def test_count_ogg_pages_counts_every_page():
    muxer = OggOpusMuxer(sample_rate=24000, serial=1)
    data = b"".join(
        muxer.push(bytes([0x78, index]) + b"\x00" * 8) for index in range(3)
    )
    data += muxer.finish()
    # 首个 push 带 OpusHead/OpusTags 两页 + 3 个音频页 + 1 个 EOS 页。
    assert _count_ogg_pages(data) == 6


def test_count_ogg_pages_ignores_trailing_partial_page():
    muxer = OggOpusMuxer(sample_rate=24000, serial=1)
    data = muxer.push(b"\x78" + b"\x00" * 8)
    assert _count_ogg_pages(data + b"OggS\x00") == 3


class _SplitTts:
    """把一段 Ogg 数据切成 7 字节小块，暴露逐 chunk 计页的回归。"""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __call__(self, *args, **kwargs):
        return self

    async def synthesize(self, text, *, turn_id, segment_seq):
        for offset in range(0, len(self._data), 7):
            yield self._data[offset : offset + 7]


async def test_run_tts_counts_pages_across_chunks(monkeypatch, capsys):
    muxer = OggOpusMuxer(sample_rate=24000, serial=1)
    data = b"".join(
        muxer.push(bytes([0x78, index]) + b"\x00" * 8) for index in range(3)
    )
    data += muxer.finish()
    monkeypatch.setattr(
        "companion.scripts.voice_dashscope_smoke.DashScopeTtsProvider",
        _SplitTts(data),
    )
    assert await _run_tts("你好", print_text=False) == 0
    assert "ogg_pages=6" in capsys.readouterr().out


class _FakeAsrStream:
    def __init__(self) -> None:
        self.sent = 0
        self.finished = False

    async def events(self):
        if False:  # pragma: no cover - 只用于满足异步生成器协议
            yield None

    async def send(self, data: bytes) -> None:
        self.sent += len(data)

    async def finish(self) -> None:
        self.finished = True

    async def close(self) -> None:
        pass


class _FakeAsrProvider:
    instances: list["_FakeAsrProvider"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.stream = _FakeAsrStream()
        _FakeAsrProvider.instances.append(self)

    def open(self, **kwargs):
        return self.stream


async def test_run_asr_passes_configured_region(monkeypatch, tmp_path):
    _FakeAsrProvider.instances.clear()
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "DASHSCOPE_REGION", "cn-shanghai")
    monkeypatch.setattr(
        "companion.scripts.voice_dashscope_smoke.DashScopeAsrProvider",
        _FakeAsrProvider,
    )
    path = tmp_path / "user.ogg"
    path.write_bytes(b"\x00" * 20)
    assert await main(["--asr-ogg", str(path)]) == 0
    (fake,) = _FakeAsrProvider.instances
    assert fake.kwargs["region"] == "cn-shanghai"
    assert fake.stream.sent == 20


def test_page_counter_handles_pages_split_across_chunks():
    muxer = OggOpusMuxer(sample_rate=24000, serial=1)
    data = b"".join(
        muxer.push(bytes([0x78, index]) + b"\x00" * 8) for index in range(3)
    )
    data += muxer.finish()
    counter = _OggPageCounter()
    for offset in range(0, len(data), 7):
        counter.feed(data[offset : offset + 7])
    assert counter.pages == 6


def test_page_counter_recovers_after_non_ogg_data():
    counter = _OggPageCounter()
    counter.feed(b"raw opus payload" * 2)  # 触发 bad_capture_pattern 并清空
    muxer = OggOpusMuxer(sample_rate=24000, serial=1)
    counter.feed(muxer.push(b"\x78" + b"\x00" * 8))
    assert counter.pages == 3


async def test_main_missing_asr_file_is_friendly(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-test")
    code = await main(["--asr-ogg", str(tmp_path / "missing.ogg")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


async def test_main_without_key_refuses_without_network(monkeypatch, capsys):
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    code = await main(["--tts", "你好"])
    assert code == 2
    captured = capsys.readouterr()
    assert "DASHSCOPE_API_KEY" in captured.err
    assert captured.out == ""


async def test_main_without_action_is_an_error(capsys):
    code = await main([])
    assert code == 2
    assert "nothing to do" in capsys.readouterr().err
