"""companion.v2 协议文档契约测试（Phase 1）。"""

from pathlib import Path

from companion.shared.catalog import get_catalog

PROTOCOL_PATH = Path(__file__).resolve().parent.parent / "docs" / "protocol_v2.md"


def read_protocol() -> str:
    return PROTOCOL_PATH.read_text(encoding="utf-8")


def test_protocol_doc_exists():
    assert PROTOCOL_PATH.exists()
    assert len(read_protocol()) > 3000


def test_protocol_declares_catalog_version():
    doc = read_protocol()
    assert get_catalog()["version"] in doc


def test_protocol_declares_all_device_messages():
    doc = read_protocol()
    for message_type in [
        "hello",
        "hello_ack",
        "action.play",
        "servo.move",
        "volume.set",
        "expression.set",
        "idle.set",
        "action.stop",
        "action.result",
        "device.report",
        "servo.calibrate_preview",
        "servo.calibrate_save",
        "voice.event",
        "voice.start",
        "voice.end",
        "voice.cancel",
        "voice.segment_played",
        "voice.segment",
        "voice.cancel_ack",
        "voice.error",
    ]:
        assert message_type in doc, message_type


def test_protocol_declares_voice_flow_rules():
    doc = read_protocol()
    assert "turn_start" in doc
    assert "barge_in" in doc
    assert "cancel_ack" in doc
    assert "invalid_frame" in doc
    assert "turn_seq" in doc


def test_protocol_declares_all_app_messages():
    doc = read_protocol()
    for message_type in [
        "remote.action.play",
        "remote.servo.move",
        "remote.volume.set",
        "remote.idle.set",
        "remote.action.stop",
        "remote.calibrate.preview",
        "remote.calibrate.save",
        "remote.get_status",
        "device.status",
        "conversation.turn_state",
        "remote.ack",
    ]:
        assert message_type in doc, message_type


def test_protocol_declares_auth_and_security_rules():
    doc = read_protocol()
    assert "Authorization: Bearer" in doc
    assert "URL Query" in doc and "禁止" in doc
    assert "只在内存短暂缓冲" in doc
    assert "跳过 TLS 校验" in doc


def test_protocol_declares_arbitration_rules():
    doc = read_protocol()
    assert "最高优先级" in doc
    assert "Last-Write-Wins" in doc
    assert "FIFO" in doc
    assert "device_offline" in doc
    assert "不缓存、不补发" in doc


def test_protocol_declares_angle_and_duration_limits():
    doc = read_protocol()
    assert "[-90, 90]" in doc
    assert "[-20, 45]" in doc
    assert "3000" in doc
    assert "5000" in doc
    assert "10 次/秒" in doc


def test_protocol_declares_audio_frame_header():
    doc = read_protocol()
    assert "0xA1" in doc
    assert "turn_seq" in doc
    assert "16000" in doc
    assert "24000" in doc
