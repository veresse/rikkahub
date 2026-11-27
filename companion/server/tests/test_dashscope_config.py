"""DashScope URL 构造与配置默认值；全程不联网。"""

from companion.server.app.audio.dashscope import (
    dashscope_asr_url,
    dashscope_tts_url,
)
from companion.server.app.config import settings

DEFAULT_ASR = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_TTS = (
    "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
)


def test_asr_url_defaults_without_workspace():
    assert dashscope_asr_url("") == DEFAULT_ASR


def test_asr_url_uses_workspace_subdomain():
    assert (
        dashscope_asr_url("ws-123")
        == "wss://ws-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )


def test_asr_url_honors_region():
    assert (
        dashscope_asr_url("ws-123", region="cn-shanghai")
        == "wss://ws-123.cn-shanghai.maas.aliyuncs.com/api-ws/v1/inference"
    )
    # 空 region 回落到默认区域，避免拼出双点域名。
    assert (
        dashscope_asr_url("ws-123", region="  ")
        == "wss://ws-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )


def test_tts_url_default():
    assert dashscope_tts_url("") == DEFAULT_TTS


def test_urls_honor_local_overrides():
    assert (
        dashscope_asr_url("ws-123", "ws://127.0.0.1:9001/asr")
        == "ws://127.0.0.1:9001/asr"
    )
    assert dashscope_tts_url("http://127.0.0.1:9002/tts") == (
        "http://127.0.0.1:9002/tts"
    )


def test_settings_have_dashscope_overrides():
    assert settings.DASHSCOPE_REGION == "cn-beijing"
    assert settings.DASHSCOPE_WORKSPACE_ID == ""
    assert settings.DASHSCOPE_ASR_WS_URL == ""
    assert settings.DASHSCOPE_TTS_HTTP_URL == ""
