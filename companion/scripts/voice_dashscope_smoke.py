"""真实 DashScope 语音烟测脚本（默认不联网，只有显式运行且配置 Key 才连接）。

用法：
    python -m companion.scripts.voice_dashscope_smoke --tts "你好"
    python -m companion.scripts.voice_dashscope_smoke --asr-ogg user_16k.ogg
    python -m companion.scripts.voice_dashscope_smoke --tts "你好" --print-text

隐私：只打印字节数 / Ogg 页数 / 事件类型 / 文本长度；不落盘、不打印音频、
不打印识别或合成全文。Key 只从环境变量（或服务端 .env）读取。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion.server.app.audio.dashscope import (  # noqa: E402
    DashScopeAsrProvider,
    DashScopeTtsProvider,
)
from companion.server.app.audio.ogg import (  # noqa: E402
    OggError,
    parse_ogg_page,
)
from companion.server.app.audio.providers import ProviderError  # noqa: E402
from companion.server.app.config import settings  # noqa: E402


def _count_ogg_pages(data: bytes) -> int:
    """统计完整 Ogg 页数；尾部的半页不计。"""
    pages = 0
    buffer = data
    while buffer:
        try:
            page, consumed = parse_ogg_page(buffer)
        except OggError:
            break
        if page is None or consumed <= 0:
            break
        pages += 1
        buffer = buffer[consumed:]
    return pages


class _OggPageCounter:
    """跨 chunk 统计完整 Ogg 页数。

    SSE/TTS 的 chunk 边界不一定对齐 Ogg 页；逐块调用 `_count_ogg_pages`
    会把跨块的页整页丢弃，因此这里保留未消费的尾部，与下一块拼接后再解析。
    """

    def __init__(self) -> None:
        self._buffer = b""
        self.pages = 0

    def feed(self, data: bytes) -> None:
        self._buffer += data
        while self._buffer:
            try:
                page, consumed = parse_ogg_page(self._buffer)
            except OggError:
                # 非 Ogg / 坏页：丢弃缓冲，避免裸 Opus 数据无限滞留。
                self._buffer = b""
                return
            if page is None or consumed <= 0:
                return
            self.pages += 1
            self._buffer = self._buffer[consumed:]



async def _run_tts(text: str, *, print_text: bool) -> int:
    provider = DashScopeTtsProvider(
        settings.DASHSCOPE_API_KEY,
        model=settings.DASHSCOPE_TTS_MODEL,
        voice=settings.DASHSCOPE_TTS_VOICE,
        url=settings.DASHSCOPE_TTS_HTTP_URL,
    )
    started = time.monotonic()
    chunks = 0
    total = 0
    counter = _OggPageCounter()
    async for chunk in provider.synthesize(
        text, turn_id="smoke-tts", segment_seq=0
    ):
        chunks += 1
        total += len(chunk)
        counter.feed(chunk)
    elapsed_ms = (time.monotonic() - started) * 1000
    print(
        f"tts: chunks={chunks} bytes={total} ogg_pages={counter.pages} "
        f"elapsed_ms={elapsed_ms:.0f}"
    )
    if print_text:
        print(f"tts: text_len={len(text)}")
    return 0


async def _run_asr(path: str) -> int:
    data = Path(path).read_bytes()
    provider = DashScopeAsrProvider(
        settings.DASHSCOPE_API_KEY,
        model=settings.DASHSCOPE_ASR_MODEL,
        url=settings.DASHSCOPE_ASR_WS_URL,
        workspace_id=settings.DASHSCOPE_WORKSPACE_ID,
        region=settings.DASHSCOPE_REGION,
    )
    stream = provider.open(
        device_id="smoke", turn_id="smoke-asr", sample_rate=16000
    )

    async def consume() -> None:
        async for event in stream.events():
            print(
                f"asr: event={event.kind} text_len={len(event.text)} "
                f"code={event.code}"
            )

    try:
        reader = asyncio.create_task(consume())
        for offset in range(0, len(data), 4096):
            await stream.send(data[offset : offset + 4096])
        await stream.finish()
        await asyncio.wait_for(reader, 10.0)
    finally:
        await stream.close()
    print(f"asr: sent_bytes={len(data)}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "真实 DashScope 语音烟测；只在显式运行且配置 "
            "DASHSCOPE_API_KEY 时联网，不保存音频。"
        )
    )
    parser.add_argument("--tts", metavar="TEXT", help="合成这段文本")
    parser.add_argument(
        "--asr-ogg",
        metavar="PATH",
        help="发送 16kHz Ogg Opus 文件并打印识别事件",
    )
    parser.add_argument(
        "--print-text",
        action="store_true",
        help="额外打印文本长度（仍不打印内容）",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.tts and not args.asr_ogg:
        print(
            "nothing to do: pass --tts TEXT and/or --asr-ogg PATH",
            file=sys.stderr,
        )
        return 2
    if not (settings.DASHSCOPE_API_KEY or "").strip():
        print(
            "DASHSCOPE_API_KEY is not set; refusing to run",
            file=sys.stderr,
        )
        return 2
    if args.asr_ogg and not Path(args.asr_ogg).is_file():
        print(f"asr ogg file not found: {args.asr_ogg}", file=sys.stderr)
        return 2

    try:
        if args.tts:
            code = await _run_tts(args.tts, print_text=args.print_text)
            if code:
                return code
        if args.asr_ogg:
            code = await _run_asr(args.asr_ogg)
            if code:
                return code
    except ProviderError as exc:
        print(f"voice provider error: {exc.code}", file=sys.stderr)
        return 1
    return 0


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
