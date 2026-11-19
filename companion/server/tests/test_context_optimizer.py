import pytest
from companion.server.app.services.orchestrator import (
    limit_context_hysteresis,
    CONTEXT_KEEP_RATIO,
    DEFAULT_CONTEXT_MESSAGE_LIMIT
)

def test_limit_context_empty():
    assert limit_context_hysteresis([], 20) == []

def test_limit_context_within_limit():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}"} for i in range(10)]
    result = limit_context_hysteresis(msgs, 20)
    assert len(result) == 10
    assert result == msgs

def test_limit_context_default_fallback():
    # 当 limit <= 0 时，应自动采用 DEFAULT_CONTEXT_MESSAGE_LIMIT (20) 兜底截断，绝不全量拉取
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}"} for i in range(50)]
    result_zero = limit_context_hysteresis(msgs, 0)
    result_neg = limit_context_hysteresis(msgs, -1)
    
    assert len(result_zero) <= DEFAULT_CONTEXT_MESSAGE_LIMIT
    assert len(result_neg) <= DEFAULT_CONTEXT_MESSAGE_LIMIT
    assert result_zero[0]["role"] == "user"

def test_hysteresis_cache_stability():
    """
    验证滞回算法的核心特性：在连续多轮对话中，截断起点必须保持绝对静止，以命中 Prompt Cache
    """
    limit = 20
    target = round(limit * CONTEXT_KEEP_RATIO)  # 12
    stride = limit - target                     # 8

    # 构造一条初始有 21 条的消息流 (刚越过 limit)
    # 偶数是 user，奇数是 assistant
    base_msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}"} for i in range(21)]
    res_21 = limit_context_hysteresis(base_msgs, limit)
    first_msg_21 = res_21[0]["content"]

    # 连续追加多轮对话 (22, 23, 24, 25, 26, 27)
    # 在 stride 内，截断起点必须 100% 相同！
    for count in range(22, 28):
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}"} for i in range(count)]
        res = limit_context_hysteresis(msgs, limit)
        assert res[0]["content"] == first_msg_21, f"第 {count} 条消息时截断起点发生移动，破坏了 Prompt Cache！"

def test_align_user_boundary():
    """
    验证截断边界必须严格对齐在 user 消息上，避免半截 assistant 回复引发模型幻觉
    """
    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ]
    # 强制较小 limit 触发截断
    res = limit_context_hysteresis(msgs, limit=3)
    assert res[0]["role"] == "user"
