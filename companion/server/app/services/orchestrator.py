import asyncio
import json
import logging
import time
import uuid
import weakref
from typing import AsyncGenerator, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from companion.server.app.config import settings
from companion.server.app.models.assistant import AssistantModel
from companion.server.app.models.conversation import ConversationModel
from companion.server.app.models.message_node import MessageNodeModel
from companion.server.app.models.memory import MemoryModel
from companion.server.app.services.action_orchestrator import ActionOrchestrator, ActionTurnState
from companion.server.app.services.action_tools import (
    build_robot_tools,
    offline_note,
    robot_tool_injection,
)
from companion.server.app.services.deepseek_client import DeepSeekClient
from companion.server.app.services.device_registry import (
    device_link_is_online,
    get_device_link,
    resolve_device_for_conversation,
)
from companion.server.app.services.events import get_event_bus, publish_async
from companion.server.app.services.search_provider import SearchProvider
from companion.server.app.services.tool_calls import ToolCallAccumulator

# 上下文截断常数：与手机端阶梯滞回机制严格对齐
CONTEXT_KEEP_RATIO = 0.6
DEFAULT_CONTEXT_MESSAGE_LIMIT = 20  # 默认最大保留 20 条消息 (约 10 轮交互)，杜绝无脑全捞
MAX_MEMORY_ITEMS = 10               # 最多注入 10 条核心记忆，防止上下文基底过重
MAX_MEMORY_CHAR_LEN = 200          # 单条记忆截断长度上限
MAX_TOOL_ROUNDS = 3                 # 单轮内最多进行 3 次工具执行回合，防止死循环

logger = logging.getLogger(__name__)


def limit_context_hysteresis(messages: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    """
    按阶梯式(滞回)策略限制上下文消息数量，极大提高大模型 Prompt Cache 命中率。
    
    核心原理：
    普通滑动窗口每多一条消息就向前滑动一条，导致上下文整个前缀每轮都发生变化，打碎 DeepSeek 的 Prompt Cache；
    滞回算法在未超过 limit 时完全不移动起点；一旦超过 limit，一次性跳过 stride 条；
    在接下来的连续 stride 轮对话中，截断起点保持绝对静止，从而最大化复用 DeepSeek 的前缀缓存，费用直降 90%。
    """
    if not messages:
        return []

    # 若 limit <= 0，使用默认的 20 条兜底保护
    effective_limit = limit if limit > 0 else DEFAULT_CONTEXT_MESSAGE_LIMIT

    if len(messages) <= effective_limit:
        return messages

    target = max(1, min(effective_limit, round(effective_limit * CONTEXT_KEEP_RATIO)))
    stride = max(1, effective_limit - target)

    # 阶梯步进计算截断点
    start_index = min(len(messages) - 1, ((len(messages) - effective_limit) // stride + 1) * stride)

    # 边界对齐：确保上下文第一条必须是 user 角色消息 (避免从半截 assistant 回复开头导致模型幻觉)
    adjusted_start = start_index
    while adjusted_start < len(messages) and messages[adjusted_start].get("role") != "user":
        adjusted_start += 1

    # 兜底：如果往后找不到 user 消息，则往前找 user 消息
    if adjusted_start >= len(messages):
        adjusted_start = start_index
        while adjusted_start > 0 and messages[adjusted_start].get("role") != "user":
            adjusted_start -= 1

    return messages[adjusted_start:]

# 会话锁保持进程级全局：语音运行时与 App 生成可能在不同线程/循环里检查
# `.locked()`（e2e 也用另一个循环模拟 App 持锁），按循环隔离会导致可见性
# 丢失。生产环境只有单事件循环，不存在跨循环阻塞获取。
_CONVERSATION_LOCKS: dict[str, asyncio.Lock] = {}

# 落库串行锁按事件循环隔离，内层用弱引用：锁被协程持有期间仍存活，闲置后
# 随循环一起回收，避免 WeakKeyDictionary“值强引用 key”导致循环永不释放。
_PERSIST_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.WeakValueDictionary[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()

# 取消落库总预算：结算在途任务 + 重试共享同一预算，避免最坏 2s+2s。
_CANCEL_TOTAL_TIMEOUT = 2.0
_CANCEL_SETTLE_TIMEOUT = 1.5

# 正在生成的 turn_key：同一 client_msg_id 的并发重试必须回 duplicate_in_progress，
# 而不是排队/覆盖。单事件循环内“检查 + 加入”之间没有 await，天然原子。
_INFLIGHT_TURNS: set[str] = set()

# 每个会话当前活跃的 turn_key：旧轮的 idle 晚于新轮 generating 时必须被丢弃。
_ACTIVE_TURN: dict[str, str] = {}


def _persist_locks_for(
    loop: asyncio.AbstractEventLoop,
) -> weakref.WeakValueDictionary[str, asyncio.Lock]:
    table = _PERSIST_LOCKS.get(loop)
    if table is None:
        table = weakref.WeakValueDictionary()
        _PERSIST_LOCKS[loop] = table
    return table


def get_conversation_lock(conv_id: str) -> asyncio.Lock:
    if conv_id not in _CONVERSATION_LOCKS:
        _CONVERSATION_LOCKS[conv_id] = asyncio.Lock()
    return _CONVERSATION_LOCKS[conv_id]


def get_persist_lock(conv_id: str) -> asyncio.Lock:
    table = _persist_locks_for(asyncio.get_running_loop())
    lock = table.get(conv_id)
    if lock is None:
        lock = asyncio.Lock()
        table[conv_id] = lock
    return lock


class ChatOrchestrator:
    @staticmethod
    def _mark_turn_active(conversation_id: str, turn_key: str) -> None:
        _ACTIVE_TURN[conversation_id] = turn_key

    @classmethod
    async def _publish_turn_state(
        cls, conversation_id: str, state: str, source: str, turn_key: str
    ) -> None:
        """广播生成状态；订阅者异常绝不影响生成主流程。"""
        try:
            await get_event_bus().publish(
                cls._turn_state_payload(conversation_id, state, source, turn_key)
            )
        except Exception:
            logger.debug(
                "turn_state publish failed: %s state=%s", conversation_id, state
            )

    @staticmethod
    def _turn_state_payload(
        conversation_id: str, state: str, source: str, turn_key: str
    ) -> dict[str, Any]:
        return {
            "v": 2,
            "type": "conversation.turn_state",
            "conversation_id": conversation_id,
            "state": state,
            "source": source,
            "turn_id": turn_key,
            "turn_key": turn_key,
        }

    @classmethod
    def _publish_turn_idle(
        cls, conversation_id: str, turn_key: str, source: str
    ) -> bool:
        """只有当前活跃轮次能置 idle，旧轮迟到事件被丢弃。

        用后台任务广播：取消路径上 finally 里的 `await` 会被再次取消，
        若同步 await 慢订阅者，App 会永久停在 generating。状态本身已在
        同一 tick 内同步更新，不依赖广播完成。
        """
        if _ACTIVE_TURN.get(conversation_id) != turn_key:
            return False
        _ACTIVE_TURN.pop(conversation_id, None)
        publish_async(
            get_event_bus(),
            cls._turn_state_payload(conversation_id, "idle", source, turn_key),
        )
        return True

    @staticmethod
    def _is_generating_status(chunk: Any) -> bool:
        return (
            isinstance(chunk, dict)
            and chunk.get("type") == "status"
            and chunk.get("status") == "generating"
        )

    @staticmethod
    def derive_turn_key(conversation_id: str, client_msg_id: str | None) -> str:
        """幂等键：同一 client_msg_id 在任意时刻重试都映射到同一轮。

        语音轮次没有 client_msg_id，保持每轮随机，不与 App 重试混用。
        """
        if client_msg_id:
            return str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"chat:{conversation_id}:{client_msg_id}",
                )
            )
        return str(uuid.uuid4())

    @staticmethod
    def message_ids(conversation_id: str, turn_key: str) -> tuple[str, str]:
        """返回 (user_message_id, assistant_message_id)，与 _save_turn 完全一致。"""
        return (
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"companion:{conversation_id}:{turn_key}:user",
                )
            ),
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"companion:{conversation_id}:{turn_key}:assistant",
                )
            ),
        )

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        parts = message.get("parts") or []
        return "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        )

    @classmethod
    def _is_replayable_turn(cls, message: dict[str, Any]) -> bool:
        """只有完整回答才能 replay；中断/空回答允许同 id 重试覆盖。"""
        return not message.get("interrupted") and bool(
            cls._message_text(message).strip()
        )

    @classmethod
    async def _find_saved_turn_message(
        cls,
        db: AsyncSession,
        conversation_id: str,
        assistant_id: str,
        *,
        scan_conversation: bool,
    ) -> tuple[MessageNodeModel, int] | None:
        """按 assistant 消息 id 定位已落库的助手消息。

        普通轮次的消息 id 同时是节点 id（快路径）。重新生成/取消落库的候选
        只存在于节点内部，需要扫一遍本会话节点；该扫描只在幂等重试时启用，
        避免语音轮次等随机 turn_key 每轮多做一次查询。
        """
        node = await db.get(MessageNodeModel, assistant_id)
        if node is not None and node.conversation_id == conversation_id:
            for index, message in enumerate(list(node.messages or [])):
                if message.get("id") == assistant_id:
                    return node, index
        if not scan_conversation:
            return None
        result = await db.execute(
            select(MessageNodeModel).where(
                MessageNodeModel.conversation_id == conversation_id
            )
        )
        for candidate in result.scalars().all():
            for index, message in enumerate(list(candidate.messages or [])):
                if message.get("id") == assistant_id:
                    return candidate, index
        return None

    @classmethod
    async def _find_replayed_turn(
        cls,
        db: AsyncSession,
        conversation_id: str,
        turn_key: str,
        regenerate_node_id: str | None,
        *,
        scan_conversation: bool = True,
    ) -> dict[str, Any] | None:
        """同一 client_msg_id 的完整回答已落库时返回 done 附加字段。

        幂等判定只看消息 id 是否已落库，不看请求是否带 `regenerate_node_id`：
        两者不一致时若按请求参数猜位置，同一条消息会被写两次。

        中断/空回答不算完成：返回 None，让同一 turn_key 重新生成，并由
        `_save_turn` 就地覆盖旧候选，否则断线重试永远只能拿到半句或空回答。
        """
        user_id, assistant_id = cls.message_ids(conversation_id, turn_key)
        found = await cls._find_saved_turn_message(
            db,
            conversation_id,
            assistant_id,
            scan_conversation=scan_conversation,
        )
        if found is None:
            return None
        node, index = found
        message = list(node.messages or [])[index]
        if not cls._is_replayable_turn(message):
            return None
        return {
            "assistant_message_id": assistant_id,
            # 普通轮次的助手消息 id 同时是节点 id；重新生成的候选没有独立用户节点。
            "user_message_id": user_id if node.id == assistant_id else None,
            "finish_reason": message.get("finishReason") or "stop",
        }

    @staticmethod
    async def build_context(
        db: AsyncSession,
        conversation_id: str,
        user_text: str
    ) -> tuple[AssistantModel, list[dict[str, str]], list[MessageNodeModel]]:
        """
        构建用于大模型的上下文消消息列表
        组装顺序: System Prompt + 记忆 -> 选定分支的历史消息 (截断) -> 当前用户提问
        """
        # 1. 查询会话与助手
        conv = await db.get(ConversationModel, conversation_id)
        if not conv:
            raise ValueError(f"会话不存在: {conversation_id}")

        assistant = await db.get(AssistantModel, conv.assistant_id)
        if not assistant:
            raise ValueError(f"关联角色不存在: {conv.assistant_id}")

        # 2. 组装 System Prompt 与记忆
        system_content_parts = []
        if assistant.system_prompt:
            system_content_parts.append(assistant.system_prompt.strip())

        if conv.custom_system_prompt:
            system_content_parts.append(conv.custom_system_prompt.strip())

        # 检索记忆 (若开启，限制最多 10 条，且单条长度受限，保证前缀稳定)
        if assistant.enable_memory:
            mem_query = select(MemoryModel)
            if assistant.use_global_memory:
                mem_query = mem_query.where((MemoryModel.assistant_id == assistant.id) | (MemoryModel.assistant_id.is_(None)))
            else:
                mem_query = mem_query.where(MemoryModel.assistant_id == assistant.id)

            # 按 updated_at 倒序取最近更新的记忆，最多 MAX_MEMORY_ITEMS 条
            mem_query = mem_query.order_by(MemoryModel.updated_at.desc()).limit(MAX_MEMORY_ITEMS)
            mem_res = await db.execute(mem_query)
            memories = list(mem_res.scalars().all())

            # 再次按 id 稳定升序排序，确保生成的 System Prompt 字符串字节级一致，绝不破坏 Prompt Cache
            memories.sort(key=lambda m: str(m.id))

            if memories:
                memory_texts = "\n".join([f"- {m.content.strip()[:MAX_MEMORY_CHAR_LEN]}" for m in memories if m.content.strip()])
                if memory_texts:
                    system_content_parts.append(f"\n【关于用户的核心记忆】\n{memory_texts}")

        system_message = {"role": "system", "content": "\n\n".join(system_content_parts)}

        # 3. 读取历史消息树节点
        node_query = select(MessageNodeModel).where(
            MessageNodeModel.conversation_id == conversation_id
        ).order_by(MessageNodeModel.node_index.asc())
        node_res = await db.execute(node_query)
        all_nodes = list(node_res.scalars().all())

        # 提取选中的历史消息
        history_messages = []
        for node in all_nodes:
            if not node.messages:
                continue
            idx = min(max(0, node.select_index), len(node.messages) - 1)
            msg = node.messages[idx]
            role = str(msg.get("role", "user")).lower()
            # 提取纯文本内容
            parts = msg.get("parts", [])
            text_content = ""
            for p in parts:
                if isinstance(p, dict) and "text" in p:
                    text_content += p["text"]
            if text_content.strip():
                history_messages.append({"role": role, "content": text_content.strip()})

        # 4. 采用阶梯式(滞回)算法进行上下文截断：固定台阶起点，最大化激活 Prompt Cache
        history_messages = limit_context_hysteresis(history_messages, assistant.context_message_limit)

        # 5. 组合全部消息
        full_messages = [system_message] + history_messages + [{"role": "user", "content": user_text}]
        return assistant, full_messages, all_nodes

    @classmethod
    async def stream_chat(
        cls,
        db: AsyncSession,
        conversation_id: str,
        user_text: str,
        regenerate_node_id: str | None = None,
        *,
        cancel_reason: str = "server_cancel",
        client: Any | None = None,
        client_msg_id: str | None = None,
        source: str = "app",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式生成入口。

        取消/客户端断开（`CancelledError`/`GeneratorExit`）时，把已经生成的
        部分回复落库并标记 `interrupted=True`，再向上抛出；正常完成由内部
        生成器自己落库。语音轮次通过 `cancel_reason="barge_in"` 让在途动作
        的 `action.stop` 使用正确原因。

        `client_msg_id` 是 App 生成的幂等键：同一 id 已落库时直接回
        `done(replayed=true)`（不调用模型），生成中重试回 `duplicate_in_progress`。
        """
        turn_key = cls.derive_turn_key(conversation_id, client_msg_id)
        idempotent = client_msg_id is not None
        replayed = await cls._find_replayed_turn(
            db,
            conversation_id,
            turn_key,
            regenerate_node_id,
            scan_conversation=idempotent,
        )
        if replayed is not None:
            yield {
                "type": "done",
                "finish_reason": replayed.pop("finish_reason", "stop"),
                "replayed": True,
                **replayed,
            }
            return
        if turn_key in _INFLIGHT_TURNS:
            yield {"type": "error", "error": "duplicate_in_progress"}
            return
        _INFLIGHT_TURNS.add(turn_key)

        snapshot: dict[str, Any] = {
            "text": "",
            "reasoning": "",
            "usage": None,
            "all_nodes": None,
            "saved": False,
            # 本轮落库幂等键：client_msg_id 存在时跨重试稳定，否则每轮随机。
            "turn_key": turn_key,
            # 只有幂等轮次才需要按消息 id 扫描历史节点。
            "idempotent": idempotent,
        }
        inner = cls._stream_chat_inner(
            db,
            conversation_id,
            user_text,
            regenerate_node_id,
            cancel_reason=cancel_reason,
            client=client,
            snapshot=snapshot,
            turn_key=turn_key,
        )
        started = False
        try:
            async for chunk in inner:
                if not started and cls._is_generating_status(chunk):
                    started = True
                    cls._mark_turn_active(conversation_id, turn_key)
                    await cls._publish_turn_state(
                        conversation_id, "generating", source, turn_key
                    )
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _CANCEL_TOTAL_TIMEOUT
            # 先关闭内层生成器：`GeneratorExit` 可能来自消费者 `aclose()`/GC，
            # 此时内层仍挂在 `async with lock` 里的 `yield` 上，必须先释放会话
            # 锁再落库，否则紧接着的新一轮会被误判为 busy。`aclose()` 幂等，
            # 下面的 `finally` 再调用一次是安全的。
            try:
                await inner.aclose()
            except Exception:
                logger.exception("closing chat stream after cancellation failed")
            saved = snapshot["saved"]
            save_task = snapshot.get("save_task")
            if not saved and save_task is not None:
                # 取消可能落在 `_save_turn()` 的 commit 期间：先有界结算该任务。
                # 它若已成功，就不能再回滚+重试（会重复整轮）。
                saved = await cls._settle_save_task(
                    save_task,
                    timeout=min(
                        _CANCEL_SETTLE_TIMEOUT,
                        max(0.05, deadline - loop.time()),
                    ),
                )
                snapshot["saved"] = saved
            if not saved and snapshot["all_nodes"] is not None:
                # 落库确认未成功：先回滚被打断的事务，再重试，避免整轮丢失。
                # 注意：这里绝不能再持有会话锁。设备 barge-in 后会在收到
                # cancel_ack 的同一 tick 立刻 voice.start，若取消落库占着锁，
                # `begin()` 会把新一轮误判为 busy（fatal）。顺序偏差由落库锁
                # 与最大下标重算兜底，见日志 §6。
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("rollback after cancellation failed")
                await cls._persist_cancelled(
                    db,
                    conversation_id=conversation_id,
                    user_text=user_text,
                    full_response_text=snapshot["text"],
                    full_reasoning_text=snapshot["reasoning"],
                    latest_usage=snapshot["usage"],
                    all_nodes=snapshot["all_nodes"],
                    regenerate_node_id=regenerate_node_id,
                    turn_key=snapshot["turn_key"],
                    idempotent=bool(snapshot.get("idempotent")),
                    timeout=max(0.05, deadline - loop.time()),
                )
            raise
        finally:
            _INFLIGHT_TURNS.discard(turn_key)
            if started:
                cls._publish_turn_idle(conversation_id, turn_key, source)
            await inner.aclose()

    @classmethod
    async def _stream_chat_inner(
        cls,
        db: AsyncSession,
        conversation_id: str,
        user_text: str,
        regenerate_node_id: str | None = None,
        *,
        cancel_reason: str = "server_cancel",
        client: Any | None = None,
        snapshot: dict[str, Any] | None = None,
        turn_key: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        全流程流式生成与落盘逻辑
        """
        if turn_key is None:
            turn_key = str(uuid.uuid4())
            if snapshot is not None:
                snapshot["turn_key"] = turn_key
        lock = get_conversation_lock(conversation_id)
        if lock.locked():
            yield {"type": "error", "error": "该对话当前正在生成回复，请稍候"}
            return

        async with lock:
            assistant, messages, all_nodes = await cls.build_context(db, conversation_id, user_text)
            if snapshot is not None:
                snapshot["all_nodes"] = all_nodes

            # 联网搜索三态：off 不搜；auto 走关键词触发；on 每轮强制。
            # 另外受服务端总开关约束（关闭时不触网、不注入检索结果）。
            search_mode = getattr(assistant, "web_search_mode", None) or (
                "auto" if assistant.enable_web_search else "off"
            )
            should_search = settings.SEARCH_ENABLED and (
                search_mode == "on"
                or (
                    search_mode == "auto"
                    and any(
                        w in user_text
                        for w in ["今天", "新闻", "天气", "最新", "查一下", "搜索"]
                    )
                )
            )
            if should_search:
                yield {"type": "status", "status": "searching", "message": "正在联网搜索实时信息..."}
                search_results = await SearchProvider.search(user_text)
                if search_results:
                    search_context = "\n".join([f"[{i+1}] {r['title']}: {r['snippet']} ({r['url']})" for i, r in enumerate(search_results)])
                    messages.insert(-1, {
                        "role": "system",
                        "content": f"【联网实时搜索结果】\n{search_context}\n请结合以上检索到的实时信息回答，并在合适处引用来源。"
                    })
                    yield {"type": "status", "status": "search_done", "results": search_results}

            client = client or DeepSeekClient()
            full_response_text = ""
            full_reasoning_text = ""
            latest_usage = None

            # 解析本轮绑定的设备，决定是否注入 robot.* 工具
            device = await resolve_device_for_conversation(db, conversation_id)
            link = get_device_link(device.id) if device is not None else None
            inject_tools, inject_reason = robot_tool_injection(
                assistant, device, online=device_link_is_online(link)
            )
            robot_tools = build_robot_tools() if inject_tools else None
            if not inject_tools and bool(getattr(assistant, "enable_action_sync", False)):
                # 设备离线/未绑定/固件不支持时不注入工具，只给模型一句状态提示。
                message = offline_note(inject_reason)
                if message:
                    messages.insert(-1, {"role": "system", "content": message})

            turn = ActionTurnState(
                turn_id=str(uuid.uuid4()),
                max_actions=settings.MAX_ACTIONS_PER_TURN,
                cancel_reason=cancel_reason,
            )
            action_orchestrator = ActionOrchestrator()
            working_messages = list(messages)
            tools_for_call = robot_tools

            yield {"type": "status", "status": "generating"}

            for round_index in range(MAX_TOOL_ROUNDS + 2):
                accumulator = ToolCallAccumulator()
                round_text = ""

                async for chunk in client.stream_chat(
                    messages=working_messages,
                    model=assistant.model_id or None,
                    temperature=assistant.temperature,
                    top_p=assistant.top_p,
                    tools=tools_for_call,
                ):
                    chunk_type = chunk.get("type")
                    if chunk_type == "content":
                        full_response_text += chunk["delta"]
                        round_text += chunk["delta"]
                        if snapshot is not None:
                            snapshot["text"] = full_response_text
                        yield chunk
                    elif chunk_type == "reasoning":
                        full_reasoning_text += chunk["delta"]
                        if snapshot is not None:
                            snapshot["reasoning"] = full_reasoning_text
                        yield chunk
                    elif chunk_type == "tool_call":
                        accumulator.add(chunk.get("delta") or [])
                    elif chunk_type == "error":
                        yield chunk
                        return
                    elif chunk_type == "usage":
                        latest_usage = chunk["usage"]
                        if snapshot is not None:
                            snapshot["usage"] = latest_usage
                        yield chunk
                calls = accumulator.build()
                if not calls:
                    break

                working_messages.append(
                    {
                        "role": "assistant",
                        "content": round_text or None,
                        "tool_calls": calls,
                    }
                )

                limit_reached = round_index >= MAX_TOOL_ROUNDS
                for index, call in enumerate(calls):
                    function = call.get("function") or {}
                    tool_name = function.get("name") or ""
                    if limit_reached:
                        result = {
                            "ok": False,
                            "error": "round_limit_exceeded",
                            "message": f"本轮工具调用超过 {MAX_TOOL_ROUNDS} 回合",
                        }
                    else:
                        try:
                            parsed_args = json.loads(function.get("arguments") or "{}")
                            if not isinstance(parsed_args, dict):
                                raise ValueError("arguments 必须是对象")
                        except (json.JSONDecodeError, ValueError):
                            result = {"ok": False, "error": "invalid_arguments"}
                        else:
                            if device is None:
                                result = {"ok": False, "error": "device_offline"}
                            else:
                                # 客户端断开/用户停止会以 CancelledError 传到
                                # `_DeviceScheduler.submit()`，由调度器只中止本轮
                                # 在途命令并下发 action.stop，不做全局急停，
                                # 因此不会打断 App 同时进行的遥控动作。
                                result = await action_orchestrator.execute_tool(
                                    tool_name, parsed_args, device.id, turn
                                )

                    yield {"type": "tool_result", "tool": tool_name, "result": result}
                    working_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"call_{index}",
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                if limit_reached:
                    # 再给模型一次纯文字收尾的机会，不再允许新的工具调用。
                    tools_for_call = None

            # 生成完成，保存到数据库。用独立任务 + shield：取消落在 commit
            # 期间时任务仍会继续，外层取消分支只需结算一次即可判断是否已落库。
            save_task = asyncio.create_task(
                cls._save_turn(
                    db,
                    conversation_id=conversation_id,
                    user_text=user_text,
                    full_response_text=full_response_text,
                    full_reasoning_text=full_reasoning_text,
                    latest_usage=latest_usage,
                    all_nodes=all_nodes,
                    regenerate_node_id=regenerate_node_id,
                    turn_key=turn_key,
                    idempotent=bool(snapshot.get("idempotent")) if snapshot else False,
                )
            )
            if snapshot is not None:
                snapshot["save_task"] = save_task
            assistant_msg = await asyncio.shield(save_task)
            if snapshot is not None:
                # 只有 commit 成功返回后才标记；取消落在 commit 中时外层仍会重试。
                snapshot["saved"] = True

            yield {
                "type": "done",
                "finish_reason": "stop",
                "assistant_message_id": assistant_msg["id"],
                "user_message_id": None if regenerate_node_id else cls.message_ids(
                    conversation_id, turn_key
                )[0],
                "replayed": False,
            }

    @staticmethod
    async def _settle_save_task(
        task: asyncio.Task, *, timeout: float = _CANCEL_SETTLE_TIMEOUT
    ) -> bool:
        """取消时结算在途落库任务：成功返回 True；超时/失败则取消并等待。

        必须在回滚/重试前调用：同一 `AsyncSession` 不能被两个协程并发使用。
        """
        if task.done():
            return not task.cancelled() and task.exception() is None
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return False

    @classmethod
    async def _save_turn(
        cls,
        db: AsyncSession,
        *,
        conversation_id: str,
        user_text: str,
        full_response_text: str,
        full_reasoning_text: str,
        latest_usage: Any,
        all_nodes: list[Any],
        regenerate_node_id: str | None,
        interrupted: bool = False,
        finish_reason: str = "stop",
        turn_key: str | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """正常结束与取消共用的落库：用户节点始终写，助手节点即使为空也写。

        `turn_key` 是本轮落库的幂等键：同一轮取消重试会复用它并命中已提交
        节点；不同轮次（包括取消后立刻开始的新一轮）使用不同键，绝不互相
        复用。命中已提交的完整回答直接返回（幂等 replay）；命中中断/空回答
        则用本次结果就地覆盖，避免重试后留下空回答且再也无法生成。
        """
        turn_key = turn_key or str(uuid.uuid4())
        user_message_id, assistant_message_id = cls.message_ids(
            conversation_id, turn_key
        )
        user_msg = {
            "id": user_message_id,
            "role": "USER",
            "parts": [{"text": user_text}],
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        assistant_msg: dict[str, Any] = {
            "id": assistant_message_id,
            "role": "ASSISTANT",
            "parts": [],
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if full_reasoning_text:
            assistant_msg["parts"].append({"reasoning": full_reasoning_text})
        assistant_msg["parts"].append({"text": full_response_text})
        if latest_usage:
            assistant_msg["tokenUsage"] = latest_usage
        if interrupted:
            assistant_msg["interrupted"] = True
            assistant_msg["finishReason"] = finish_reason

        # 注意：build_context / 设备解析已经开启了自动事务，这里不能再用
        # `async with db.begin()`，改为显式 commit。
        async with get_persist_lock(conversation_id):
            if regenerate_node_id:
                # 重新生成某一轮：将新回复追加为候选分支，更新 select_index。
                # 同一 turn_key 的重试若已追加过完整回答，直接复用；若上次是
                # 中断/空回答，则覆盖该候选（保留分支位置），不做重复追加。
                node = await db.get(MessageNodeModel, regenerate_node_id)
                if node:
                    messages = list(node.messages or [])
                    existing_index = next(
                        (
                            index
                            for index, message in enumerate(messages)
                            if message.get("id") == assistant_msg["id"]
                        ),
                        None,
                    )
                    if existing_index is not None and cls._is_replayable_turn(
                        messages[existing_index]
                    ):
                        return messages[existing_index]
                    if existing_index is not None:
                        messages[existing_index] = assistant_msg
                        node.messages = messages
                        node.select_index = existing_index
                    else:
                        node.messages = [*messages, assistant_msg]
                        node.select_index = len(node.messages) - 1
            else:
                found = await cls._find_saved_turn_message(
                    db,
                    conversation_id,
                    assistant_msg["id"],
                    scan_conversation=idempotent,
                )
                if found is not None:
                    node, index = found
                    messages = list(node.messages or [])
                    if cls._is_replayable_turn(messages[index]):
                        return messages[index]
                    # 中断/空回答的重试：用本次完整回复覆盖同一条消息。
                    messages[index] = assistant_msg
                    node.messages = messages
                    node.select_index = index
                else:
                    next_index = await cls._next_node_index(db, conversation_id)
                    user_node = MessageNodeModel(
                        id=user_msg["id"],
                        conversation_id=conversation_id,
                        node_index=next_index,
                        messages=[user_msg],
                        select_index=0,
                    )
                    db.add(user_node)

                    assistant_node = MessageNodeModel(
                        id=assistant_msg["id"],
                        conversation_id=conversation_id,
                        node_index=next_index + 1,
                        messages=[assistant_msg],
                        select_index=0,
                    )
                    db.add(assistant_node)

            # 更新会话更新时间
            conv = await db.get(ConversationModel, conversation_id)
            if conv:
                conv.updated_at = int(time.time() * 1000)
            await db.commit()
            return assistant_msg

    @staticmethod
    def _select_message(
        node: MessageNodeModel, fallback: dict[str, Any]
    ) -> dict[str, Any]:
        messages = list(node.messages or [])
        if not messages:
            return fallback
        index = min(max(node.select_index, 0), len(messages) - 1)
        return messages[index]

    @staticmethod
    async def _next_node_index(db: AsyncSession, conversation_id: str) -> int:
        """落库锁内读取当前最大下标；兼容历史数据中的空档。"""
        result = await db.execute(
            select(func.max(MessageNodeModel.node_index)).where(
                MessageNodeModel.conversation_id == conversation_id
            )
        )
        current = result.scalar()
        return 0 if current is None else int(current) + 1

    @classmethod
    async def _persist_cancelled(
        cls,
        db: AsyncSession,
        *,
        timeout: float = _CANCEL_TOTAL_TIMEOUT,
        **kwargs: Any,
    ) -> None:
        """取消/断线时用独立任务落库，受取消总预算约束，失败只记日志。"""
        task = asyncio.create_task(
            cls._save_turn(
                db,
                **kwargs,
                interrupted=True,
                finish_reason="interrupted",
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            logger.warning("interrupted turn persistence timed out")
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception:
            logger.exception("interrupted turn persistence failed")
