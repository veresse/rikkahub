# Companion App 4B（角色 + 会话 + 流式聊天 + App WS）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 4A 的登录会话之上，交付“能管理角色、能流式聊天、能看到语音事件并纠正转写、能看到会话生成中状态”的可用纵向切片。

**Architecture:** 服务端仍为 FastAPI + 单 worker，新增字段走 Alembic 0003；聊天幂等键由 `client_msg_id` 派生，生成中/终态经全局 `EventBus` 广播 `conversation.turn_state`；语音管线补齐 `user_message_id` 与 `conversation_id`。App 把 4A 的 `AuthApiFactory` 扩展为覆盖全部 REST + SSE 的 `CompanionApi` 工厂，新增手写 Repository、SSE 解析、`AppWsManager`（可注入连接器与重连策略）以及角色/会话/聊天/记忆四个页面。

**Tech Stack:** 同 4A（Kotlin 2.4.21、AGP 9.4.0、Compose BOM 2026.09.00、Retrofit 3.0.0 + OkHttp 5.5.0、kotlinx.serialization 1.12.0、JUnit4 + kotlinx-coroutines-test）；服务端 Python 3.11 + pytest。

**Spec:** `docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（§7.2、§7.3、§8、§9 的 4B 行、§13 第 2/3/4/8 条）、`companion/docs/protocol_v2.md`（§5.3、§8.2、§8.3）

## Global Constraints

- 根 RikkaHub 工程只读：不得修改根 `settings.gradle.kts`、根 `build.gradle.kts`、`app/`、根 `gradle/`。
- `minSdk = 26`；`targetSdk = 35`；`compileSdk` 用 `release(37) { minorApiLevel = 2 }`。
- 严禁自定义 `X509TrustManager`/`HostnameVerifier`；release 明文流量关闭；debug 仅 `localhost`/`127.0.0.1`/`10.0.2.2`。
- App 内不得出现任何模型/ASR/TTS/搜索 Key；Token 只以 Keystore 密文落盘；日志不打印 Token/口令/密文/完整请求体/聊天正文。
- 所有用户可见文案为中文；错误必须可读，禁止无限转圈；离线/未登录时相关控件不得可点。
- `remote.ack` 只代表入队，不得当作动作成功（4C 才用，但 4B 的 WS 事件模型不得破坏该语义）。
- App 单测全部 JVM 测试，HTTP 用 JDK `com.sun.net.httpserver.HttpServer`；WS 用注入的假连接器（不依赖真实网络）。
- 服务端新端点错误体统一 `{"detail": "<code>", "message": "<可读文本>"}`；旧端点不改格式。
- Python 改动必须以既有 452 条测试全绿为前提；解释器固定
  `C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe -m pytest`（从仓库根运行）。
- 每个任务结束 `git status --porcelain` 干净（`companion/app/local.properties` 被忽略）。
- 不实现：记忆自动总结（UI 置灰“未实现”）、纠正后自动重生成、聊天历史离线缓存、推送通知。

## Review Focus

1. **SSE 分帧与编码**：服务端每个事件是 `data: <json>\n\n`，网络读到的块可能把一行/一个多字节字符切断；解析器必须按行缓冲，`\r\n` 与 `\n` 都要处理，坏 JSON 只丢弃不崩溃。
2. **幂等与取消的交叉**：同一 `client_msg_id` 在“生成中 / 已落库 / 客户端断开后落库中”三种时刻重试，分别应得到 `duplicate_in_progress` / `done(replayed=true)` / 正常生成，且取消落库的旧轮不得把新轮置 idle。
3. **`done` 的 `replayed` 语义**：App 收到 `replayed=true` 必须重拉会话，不能把已存在内容再追加一遍。
4. **`conversation.turn_state` 乱序**：旧轮 idle 晚于新轮 generating 时必须被服务端 `turn_key` 守卫丢弃；App 端也要用 `turn_key` 二次守卫。
5. **WS 4401 与普通断线**：只有 4401 清退且不重连；1011/1012/网络错误必须退避重连，网络恢复立即重试。
6. **转写纠正的权限与归属**：只能改属于该 `conversation_id` / `node_id` 的 `USER` 消息，只改 text part，其他 part 保留。

---

### Task 1: 角色字段扩展（`model_id`、`web_search_mode`）与模型/搜索透传

**Files:**
- Create: `companion/server/migrations/versions/0003_assistant_model_search.py`
- Modify: `companion/server/app/models/assistant.py`
- Modify: `companion/server/app/routers/assistants.py`
- Modify: `companion/server/app/services/orchestrator.py`（模型透传 + 三态搜索判定）
- Modify: `companion/server/app/importer/parser.py`、`companion/server/app/importer/service.py`、`companion/server/app/routers/import_export.py`
- Modify: `companion/server/tests/test_migrations.py`
- Create: `companion/server/tests/test_assistants_api.py`
- Modify: `companion/server/tests/test_chat_action_loop.py`（新增模型/搜索用例）

**Interfaces:**
- Consumes: 现有 `AssistantModel`、`AssistantCreateOrUpdate`、`ChatOrchestrator._stream_chat_inner`。
- Produces:
  - 列 `assistants.web_search_mode: str`（`"off"|"auto"|"on"`，非空，默认 `"auto"`；回填规则 `enable_web_search=1 → auto`、`0 → off`）。
  - `GET/POST/PUT /api/assistants` 响应与请求新增 `model_id: str | None`、`web_search_mode: str`。
  - 请求未带 `web_search_mode` 时按 `enable_web_search` 推导（兼容旧客户端）；任何写入都同步
    `enable_web_search = (web_search_mode != "off")`。
  - `ChatOrchestrator` 调 `client.stream_chat(..., model=assistant.model_id or None, ...)`。
  - 搜索：`off` 不搜；`auto` 走现有关键词；`on` 每轮必搜。

- [ ] **Step 1: 写迁移与 API 的失败测试**

`test_migrations.py` 增加 `test_0003_backfills_web_search_mode`：upgrade 到 `0002_v2_actions_auth`，插入两个角色
（`enable_web_search=1/0`），upgrade head 后分别断言 `web_search_mode == "auto"/"off"`；并断言
`"web_search_mode" in table_columns(db_path, "assistants")`。

`test_assistants_api.py`（`pytest.mark.asyncio`，用 `client`/`auth_headers` fixture）：

```python
async def test_create_and_read_model_and_search_mode(client, auth_headers):
    body = {"name": "夏以昼", "model_id": "deepseek-chat", "web_search_mode": "on"}
    created = await client.post("/api/assistants", json=body, headers=auth_headers)
    aid = created.json()["id"]
    got = (await client.get(f"/api/assistants/{aid}", headers=auth_headers)).json()
    assert got["model_id"] == "deepseek-chat"
    assert got["web_search_mode"] == "on"
    assert got["enable_web_search"] is True

async def test_legacy_enable_web_search_maps_to_auto(client, auth_headers):
    created = await client.post("/api/assistants", json={"name": "旧客户端", "enable_web_search": True}, headers=auth_headers)
    got = (await client.get(f"/api/assistants/{created.json()['id']}", headers=auth_headers)).json()
    assert got["web_search_mode"] == "auto"

async def test_invalid_search_mode_is_422(client, auth_headers):
    resp = await client.post("/api/assistants", json={"name": "坏值", "web_search_mode": "sometimes"}, headers=auth_headers)
    assert resp.status_code == 422
```

- [ ] **Step 2: 运行并确认失败**

Run: `… -m pytest companion/server/tests/test_migrations.py companion/server/tests/test_assistants_api.py -q`
Expected: FAIL（`web_search_mode` 列/字段不存在）。

- [ ] **Step 3: 实现迁移、模型、路由**

迁移 `0003`（`down_revision = "0002_v2_actions_auth"`）：

```python
with op.batch_alter_table("assistants") as batch:
    batch.add_column(sa.Column("web_search_mode", sa.String(length=8), nullable=False, server_default="off"))
op.execute("UPDATE assistants SET web_search_mode = CASE WHEN enable_web_search THEN 'auto' ELSE 'off' END")
```

`AssistantModel` 增加 `web_search_mode: Mapped[str] = mapped_column(String(8), default="auto")`。

路由：`AssistantCreateOrUpdate` 增加
`model_id: str | None = None`、`web_search_mode: Literal["off", "auto", "on"] | None = None`；
`_mode(data)` 返回 `data.web_search_mode or ("auto" if data.enable_web_search else "off")`；三处响应统一带
`model_id` 与 `web_search_mode`；创建/更新同步写 `enable_web_search = mode != "off"`。

`test_migrations.py` 原有的 `downgrade(cfg, "-1")` 断言改为：先 `downgrade -1` 断言 `web_search_mode` 消失，
再 `downgrade -1` 断言 `app_tokens`/`enable_action_sync` 消失，最后 `upgrade head`。

- [ ] **Step 4: 写模型透传与三态搜索测试（先红）**

在 `test_chat_action_loop.py` 增加：

```python
async def test_assistant_model_id_is_passed_to_client(session):
    # 角色 model_id="deepseek-reasoner"，FakeClient 记录 model
    ...  # 断言 client.models == ["deepseek-reasoner"]

async def test_web_search_modes(session, monkeypatch):
    # off：即使问题含“今天”也不调用 SearchProvider.search
    # auto：含“今天”才调用
    # on：不含关键词也调用
```

用 `monkeypatch.setattr(SearchProvider, "search", fake_search)` 计数；FakeClient 增加 `self.models` 记录。

- [ ] **Step 5: 实现透传与三态**

`_stream_chat_inner` 内：

```python
mode = getattr(assistant, "web_search_mode", None) or ("auto" if assistant.enable_web_search else "off")
should_search = mode == "on" or (mode == "auto" and any(w in user_text for w in [...]))
if should_search: ...
client.stream_chat(messages=..., model=assistant.model_id or None, ...)
```

importer：`parse_settings` 增加 `"web_search_mode": "auto" if enableWebSearch else "off"`；
`service.py` 更新/创建时写该字段；`import_export.py` 同时写 `enableWebSearch` 与 `webSearchMode`。

- [ ] **Step 6: 全量回归并提交**

Run: `… -m pytest companion/server/tests/test_assistants_api.py companion/server/tests/test_migrations.py companion/server/tests/test_chat_action_loop.py -q`，再全量 `… -m pytest -q`。
Expected: 新增用例通过，全量 452+N passed。

```bash
git add companion/server
git commit -m "feat(server): expose assistant model and web search mode"
```

---

### Task 2: 聊天幂等（`client_msg_id`）、`done` 双消息 id 与 `duplicate_in_progress`

**Files:**
- Modify: `companion/server/app/services/orchestrator.py`
- Modify: `companion/server/app/routers/chat.py`
- Create: `companion/server/tests/test_chat_idempotency.py`
- Modify: `companion/server/tests/test_chat_cancel.py`（如签名变化影响）

**Interfaces:**
- Consumes: Task 1 的 `stream_chat`；现有 `_save_turn` / `_next_node_index` / `get_conversation_lock`。
- Produces:
  - `ChatOrchestrator.derive_turn_key(conversation_id: str, client_msg_id: str | None) -> str`
    （`client_msg_id` 非空时 `uuid5(NAMESPACE_URL, f"chat:{cid}:{client_msg_id}")`，否则 `uuid4()`）。
  - `ChatOrchestrator.message_ids(conversation_id: str, turn_key: str) -> tuple[str, str]`
    返回 `(user_message_id, assistant_message_id)`，公式与 `_save_turn` 完全一致。
  - `stream_chat(..., client_msg_id: str | None = None)`：已落库 → `done(replayed=true)`；同 id 在途 →
    `{"type":"error","error":"duplicate_in_progress"}`；正常完成 `done` 恒带 `user_message_id` 与
    `assistant_message_id`、`replayed`（false）。
  - `POST /api/chat/completions` 请求体新增 `client_msg_id: str | None`（长度 1..128）。
  - 语音轮次（无 `client_msg_id`）行为与现状一致（每轮随机 key）。

- [ ] **Step 1: 写失败测试**

`test_chat_idempotency.py`：

```python
async def test_replay_same_client_msg_id_does_not_call_model(session):
    # 第一次：client_msg_id="m-1" 完整生成并落库
    # 第二次：同 id → 事件只有一个 done，client.calls == 1
    # done["replayed"] is True，且 user/assistant id 与第一次相同

async def test_same_client_msg_id_while_running_returns_duplicate_in_progress(session):
    # 用 BlockingClient 卡住第一轮；并发发起同 id 第二轮
    # 第二轮事件 == [{"type":"error","error":"duplicate_in_progress"}]
    # 放行第一轮后仍正常 done

async def test_different_client_msg_id_keeps_legacy_busy_error(session):
    # 第一轮在途，第二个不同 id → error 文本仍含“正在生成”

async def test_done_always_carries_user_message_id(session):
    # 正常完成：done["user_message_id"] == 持久化 USER 消息 id
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest companion/server/tests/test_chat_idempotency.py -q`
Expected: FAIL（参数不存在 / 无 `replayed`）。

- [ ] **Step 3: 实现**

- `_save_turn` 的 user/assistant id 改为调用 `message_ids`，行为不变。
- `stream_chat` 顶部（首个 `await` 前）：
  ```python
  turn_key = cls.derive_turn_key(conversation_id, client_msg_id)
  replayed = await cls._find_replayed_turn(db, conversation_id, turn_key, regenerate_node_id)
  if replayed is not None:
      yield {"type": "done", "finish_reason": "stop", "replayed": True, **replayed}
      return
  if turn_key in _INFLIGHT_TURNS:
      yield {"type": "error", "error": "duplicate_in_progress"}
      return
  _INFLIGHT_TURNS.add(turn_key)
  ```
  外层 `finally` 中 `_INFLIGHT_TURNS.discard(turn_key)`。
- `_find_replayed_turn`：`regenerate_node_id` 时在节点 `messages` 里找 deterministic assistant id；否则
  `db.get(MessageNodeModel, assistant_id)`，命中返回 `{"assistant_message_id": ..., "user_message_id": ...}`。
- `done`（正常完成）改为
  `{"type":"done","finish_reason":"stop","assistant_message_id":am_id,"user_message_id":user_id,"replayed":False}`。
- `chat.py`：`client_msg_id: str | None = Field(default=None, min_length=1, max_length=128)`，透传。
- 快照 `turn_key` 初始化改用派生值。

- [ ] **Step 4: 定向 + 取消回归**

Run: `… -m pytest companion/server/tests/test_chat_idempotency.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_chat_action_loop.py -q`
Expected: 全绿（取消/重试语义不变）。

- [ ] **Step 5: 提交**

```bash
git add companion/server
git commit -m "feat(server): idempotent chat turns keyed by client_msg_id"
```

---

### Task 3: `conversation.turn_state` 广播与语音事件补齐

**Files:**
- Modify: `companion/server/app/services/orchestrator.py`
- Modify: `companion/server/app/audio/binding.py`
- Modify: `companion/server/app/audio/pipeline.py`
- Create: `companion/server/tests/test_turn_state.py`
- Modify: `companion/server/tests/test_audio_pipeline.py`

**Interfaces:**
- Consumes: `get_event_bus()`；Task 2 的 `turn_key` / `done.user_message_id`。
- Produces:
  - `stream_chat(..., source: str = "app")`；`source="voice"` 由 `binding.chat_factory` 传入。
  - 广播 `{"v":2,"type":"conversation.turn_state","conversation_id":cid,"state":"generating"|"idle",
    "source":source,"turn_id":turn_key,"turn_key":turn_key}`；仅锁内生成时广播 generating，终态 idle 只由
    “当前活跃 key 相同”的轮次广播。
  - `voice.event: asr_final` 带 `conversation_id`；`turn_end` 带 `user_message_id`（未知时省略）。

- [ ] **Step 1: 写失败测试**

`test_turn_state.py`：

```python
async def test_app_turn_broadcasts_generating_then_idle(session):
    # 订阅 get_event_bus()，跑完一轮
    # 断言 states == ["generating", "idle"]，source == "app"，两个 turn_key 相同

async def test_replayed_turn_does_not_broadcast(session):
    # 同 client_msg_id 第二次 → 无 turn_state 事件

async def test_old_turn_idle_does_not_clear_new_turn(session):
    # 第一轮在途时不能起第二轮（会话锁 busy），因此直接测内部守卫：
    # _mark_turn_active(cid, "new-key") 后再 _publish_idle(cid, "old-key") → 事件数不变

async def test_voice_turn_reports_source_voice(session):
    # binding.chat_factory(..., source 由实现固定) 或直接 stream_chat(source="voice")
    # 断言事件 source == "voice"
```

`test_audio_pipeline.py` 增加：scripted chat 收到 `done`（含 `user_message_id="u-1"`）后，
`bus.only("turn_end")[0]["user_message_id"] == "u-1"`；`asr_final` 事件含 `conversation_id`。

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest companion/server/tests/test_turn_state.py companion/server/tests/test_audio_pipeline.py -q`
Expected: FAIL（无事件/无字段）。

- [ ] **Step 3: 实现**

- orchestrator：模块级 `_ACTIVE_TURN: dict[str, str]`；`_publish_turn_state(event_bus, ...)` 内部 `try/except`
  吞掉发布异常（不得影响生成）。`_stream_chat_inner` 在 `async with lock:` 后 `_ACTIVE_TURN[cid]=turn_key`
  并广播 generating；`try: ... finally:` 内若 `_ACTIVE_TURN.get(cid)==turn_key` 则删除并广播 idle。
  早退（replay/duplicate/busy）不广播。
- `binding.chat_factory` 调 `ChatOrchestrator.stream_chat(..., source="voice")`。
- pipeline：`asr_final` 加 `conversation_id=self.conversation_id`；新增 `self._turn_user_message_id`，
  在 `_start_turn` 置空，chunk 循环中 `done` 时保存；`turn_end` 与 `_complete_turn` 带
  `user_message_id=self._turn_user_message_id`（为 None 时不传该键）。

- [ ] **Step 4: 定向回归**

Run: `… -m pytest companion/server/tests/test_turn_state.py companion/server/tests/test_audio_pipeline.py companion/server/tests/test_audio_binding.py companion/server/tests/test_app_gateway.py -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add companion/server
git commit -m "feat(server): broadcast turn state and expose voice message ids"
```

---

### Task 4: `PUT` 纠正已落库 USER 消息文本

**Files:**
- Modify: `companion/server/app/routers/conversations.py`
- Create: `companion/server/tests/test_message_correction.py`

**Interfaces:**
- Consumes: `MessageNodeModel`（`messages` JSON 列表）。
- Produces: `PUT /api/conversations/{conv_id}/nodes/{node_id}/messages/{message_id}`，body
  `{"text": "<非空，≤20000>"}`；成功 `{"status":"updated","node_id":...,"message_id":...,"text":...}`；
  失败 `404 node_not_found` / `404 message_not_found` / `400 not_user_message`，均为
  `{"detail","message"}`；只替换第一个 text part，其他 part 保留。

- [ ] **Step 1: 写失败测试**

```python
async def test_update_user_message_text_only(client, auth_headers):
    # 造角色/会话/节点（role=USER，parts=[{"text":"旧"},{"image":{}}]）
    # PUT 后 GET 详情：text == "新"，image part 仍在，node 数量不变

async def test_rejects_non_user_message(client, auth_headers):
    # ASSISTANT 消息 → 400 detail == "not_user_message"

async def test_rejects_wrong_conversation_or_node(client, auth_headers):
    # node 不属于 conv → 404 node_not_found
    # message 不在 node → 404 message_not_found

async def test_empty_text_is_422(client, auth_headers):
    # body {"text": ""} → 422（422 数组格式，App 映射 validation_error）
```

- [ ] **Step 2: 运行确认失败**

Run: `… -m pytest companion/server/tests/test_message_correction.py -q`
Expected: FAIL（404/405，无端点）。

- [ ] **Step 3: 实现**

`conversations.py` 增加 `MessageTextUpdate(BaseModel)`（`Field(min_length=1, max_length=20000)`）与端点：
先 `db.get(MessageNodeModel, node_id)` 校验 `node.conversation_id == conv_id`；复制 `list(node.messages)`，
按 `id` 找 dict；校验 `role == "USER"`；构造新 parts 后 `node.messages = new_messages`、`async with db.begin()`
外不要重复开启事务（沿用 `db.get` 已开事务 → 直接 `await db.commit()`，与 `_save_turn` 一致）。

- [ ] **Step 4: 定向 + 全量**

Run: `… -m pytest companion/server/tests/test_message_correction.py companion/server/tests/test_conversations_api.py -q`，再全量 `… -m pytest -q`。
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add companion/server
git commit -m "feat(server): allow correcting persisted user message text"
```

---

### Task 5: App REST 层扩展（角色/会话/记忆/纠错）与 Repository

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/AssistantDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/ChatDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/ConversationDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/dto/MemoryDtos.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/api/CompanionApi.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/data/api/AuthApiFactory.kt`（改名 `RetrofitApiFactory`，实现 `AuthApiFactory` + `CompanionApiFactory`）
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/ApiCall.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/AssistantRepository.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/ConversationRepository.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/MemoryRepository.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Create tests: `companion/app/src/test/java/me/rerere/companion/data/repo/AssistantRepositoryTest.kt`、
  `ConversationRepositoryTest.kt`、`MemoryRepositoryTest.kt`
- Modify: `companion/app/src/test/java/me/rerere/companion/data/api/AuthApiTest.kt`（工厂改名）

**Interfaces:**
- Consumes: 4A `SessionManager`（`state.value?.serverUrl`）、`ErrorMapper`、`AppResult`；JDK `TestHttpServer`。
- Produces:
  - `interface CompanionApiFactory { fun create(baseUrl: String): CompanionApi }`
  - `class RetrofitApiFactory(sessionProvider, onUnauthorized) : AuthApiFactory, CompanionApiFactory`
  - `interface CompanionApi`（Retrofit）：
    - `listAssistants()`, `createAssistant(AssistantUpsert): CreatedDto`, `updateAssistant(id, AssistantUpsert)`, `deleteAssistant(id)`
    - `listConversations(assistantId: String?)`, `createConversation(ConversationCreate): CreatedDto`, `deleteConversation(id)`, `conversationDetail(id): ConversationDetailDto`, `selectBranch(cid, nid, index)`
    - `correctMessage(cid, nid, mid, MessageTextUpdate)`
    - `listMemories(assistantId: String?)`, `createMemory(MemoryUpsert): CreatedDto`, `updateMemory(id, MemoryUpsert)`, `deleteMemory(id)`
    - `@Streaming @POST("api/chat/completions") suspend fun chat(@Body ChatRequestDto): ResponseBody`（Task 6 使用）
  - `AssistantRepository`：`list(): AppResult<List<AssistantDto>>`、`create/update/delete`；
  - `ConversationRepository`：`list(assistantId)`、`create(assistantId, title="新对话")`、`detail(id)`、`delete(id)`、`selectBranch(...)`、`correctMessage(...)`；
  - `MemoryRepository`：`list(assistantId)`、`create/update/delete`。
  - `AppContainer` 暴露 `assistantRepository`、`conversationRepository`、`memoryRepository`。
  - DTO 中可选字段必须给默认值；`Json` 继续 `ignoreUnknownKeys = true`、`encodeDefaults = true`。

- [ ] **Step 1: 写失败测试（3 个 Repository）**

`AssistantRepositoryTest`（JDK HttpServer，返回固定 JSON；请求断言 `model_id`/`web_search_mode` 在 body 中）：

```kotlin
@Test fun `list maps assistants`() = runBlocking { ... assertEquals("deepseek-chat", items[0].modelId) }
@Test fun `create sends model and search mode`() = runBlocking { ... }
@Test fun `delete maps 404 detail to readable message`() = runBlocking { ... }
@Test fun `list maps 401 via error mapper`() = runBlocking { ... }
```

`ConversationRepositoryTest`：`list` 查询串带 `assistant_id`；`detail` 展开 `nodes/messages/select_index`；
`correctMessage` 使用 `PUT` 且 body 仅 `{"text":...}`；`selectBranch` query `index`。

`MemoryRepositoryTest`：`list` 查询串、`create/update/delete`、中文 `detail` 原样显示。

- [ ] **Step 2: 运行确认失败**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*RepositoryTest" --console=plain`
Expected: 编译失败（类不存在）。

- [ ] **Step 3: 实现 DTO / API / 工厂 / Repository**

`ApiCall.kt`：

```kotlin
abstract class SessionApi(
    protected val apiFactory: CompanionApiFactory,
    protected val sessionManager: SessionManager,
) {
    protected suspend fun <T> call(block: suspend (CompanionApi) -> T): AppResult<T>
}
```
`call` 规则：无会话 → `Err(Http("not_logged_in","请先登录"))`；`CancellationException` 先 rethrow；
`HttpException` → `ErrorMapper.map(code, errorBody, Retry-After)`；`IOException` → `AppError.Network("无法连接服务器")`。
不在 UI 层拼 URL。

工厂：把 4A 的 `RetrofitAuthApiFactory` 改名为 `RetrofitApiFactory`，保留同一 `notifiedToken`、
`buildClient()`（connect 10s / read 15s / write 15s）与 `json`；`create()` 返回 `AuthApi`，
`createCompanion()` 返回 `CompanionApi`（同一 client）。`AuthRepository` 只用 `AuthApiFactory` 接口不变。

- [ ] **Step 4: 运行测试通过**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*RepositoryTest" --console=plain`
Expected: BUILD SUCCESSFUL。

- [ ] **Step 5: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): add assistant, conversation and memory repositories"
```

---

### Task 6: SSE 解析与 `ChatRepository`（流式、取消、幂等重试）

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/sse/SseParser.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/sse/ChatEvent.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/repo/ChatRepository.kt`
- Create tests: `companion/app/src/test/java/me/rerere/companion/data/sse/SseParserTest.kt`、
  `companion/app/src/test/java/me/rerere/companion/data/repo/ChatRepositoryTest.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`

**Interfaces:**
- Consumes: Task 5 的 `CompanionApi.chat`、`SessionApi`。
- Produces:
  - `sealed interface ChatEvent`：`Content(delta)`、`Reasoning(delta)`、`Status(status, message)`、
    `ToolResult(tool, resultJson)`、`Usage(json)`、`Failed(code, message)`、`Done(finishReason,
    assistantMessageId, userMessageId, replayed)`。
  - `class SseParser { fun feed(chunk: String): List<ChatEvent> }`：按行缓冲；忽略 `:` 注释与空 `data`；
    多行 `data` 用 `\n` 拼接；坏 JSON 丢弃；未知 `type` 忽略。
  - `interface ChatRepository { fun stream(request: ChatRequestDto): Flow<ChatEvent>; suspend fun correctMessage(...): AppResult<Unit> }`
  - `HttpChatRepository(apiFactory, sessionManager)`：`flow { ... }.flowOn(Dispatchers.IO)`；
    无会话发 `Failed("not_logged_in", ...)`；IOException 发 `Failed("network", ...)`；
    协程取消时 `ResponseBody.close()`（`use`）并停止。
  - `AppContainer.chatRepository`。

- [ ] **Step 1: 写失败测试**

`SseParserTest`：

```kotlin
@Test fun `parses content and done across split chunks`() // 把 "data: {...}\n\ndata: {...}\n\n" 切成 3 段喂
@Test fun `handles crlf and multiline data`()
@Test fun `ignores comments and bad json`()
@Test fun `maps error string and replayed done`()
```

`ChatRepositoryTest`（HttpServer 用 `sendResponseHeaders(200, 0)` 流式写两帧后关闭）：

```kotlin
@Test fun `streams content then done with ids`()
@Test fun `replayed done is surfaced`()
@Test fun `cancelling the flow stops reading`() // take(1) 后服务器继续写不阻塞测试
@Test fun `http error becomes failed`() // 401/429 detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*SseParserTest" --tests "*ChatRepositoryTest" --console=plain`
Expected: 编译失败。

- [ ] **Step 3: 实现**

`SseParser.feed` 核心：`buffer.append(chunk)`，循环找 `\n`，`line = removeBefore(index).removeSuffix("\r")`；
空行且 `dataLines` 非空 → 解析并 clear；`data:` 前缀（含可选空格）→ 追加。字段解析用
`Json { ignoreUnknownKeys = true }` + `JsonObject` 手动读（`error` 兼容字符串）。

`HttpChatRepository.stream`：

```kotlin
flow {
    val session = sessionManager.state.value ?: run { emit(ChatEvent.Failed("not_logged_in", "请先登录")); return@flow }
    val body = try { apiFactory.create(session.serverUrl).chat(request) }
        catch (e: CancellationException) { throw e }
        catch (e: HttpException) { emit(...); return@flow }
        catch (e: IOException) { emit(...); return@flow }
    body.use { b ->
        val source = b.source()
        val parser = SseParser()
        while (currentCoroutineContext().isActive) {
            val line = source.readUtf8Line() ?: break
            parser.feed(line + "\n").forEach { emit(it) }
        }
        parser.feed("\n").forEach { emit(it) }  // 收尾未分帧的最后一帧
    }
}.flowOn(Dispatchers.IO)
```

- [ ] **Step 4: 运行测试通过**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*SseParserTest" --tests "*ChatRepositoryTest" --console=plain`
Expected: BUILD SUCCESSFUL。

- [ ] **Step 5: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): stream chat over sse with parsing and cancellation"
```

---

### Task 7: `AppWsManager`（重连状态机、4401 清退、turn_state 守卫）

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/data/ws/WsTransport.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/ws/WsEvents.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/ws/OkHttpWsConnector.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/data/ws/AppWsManager.kt`
- Create: `companion/app/src/test/java/me/rerere/companion/data/ws/AppWsManagerTest.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/core/AppContainer.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppNav.kt`（登录后 start / 登出 stop）

**Interfaces:**
- Consumes: `SessionManager`、`StoredSession`。
- Produces:
  - `sealed interface WsFrame { Text(json); Closed(code, reason); Failed(cause) }`
  - `fun interface WsConnector { fun connect(url: String, token: String): Flow<WsFrame> }`
  - `class AppWsManager(connector, sessionProvider: () -> StoredSession?, onUnauthorized: (token: String) -> Unit, scope: CoroutineScope, jitter: (Long) -> Long = ...)`
    - `fun start()`、`fun stop()`、`fun onNetworkAvailable()`
    - `val connection: StateFlow<ConnectionState>`（`Stopped/Connecting/Connected/Retrying(attempt, delayMillis)`）
    - `val devices: StateFlow<Map<String, DeviceStatus>>`
    - `val turnStates: StateFlow<Map<String, TurnState>>`（只保留 generating，idle 时移除）
    - `val events: SharedFlow<WsEvent>`
  - `data class TurnState(conversationId, state, source, turnKey)`；守卫：`idle` 且已有不同 `turnKey` → 丢弃。
  - URL：`http→ws`、`https→wss`，`trimEnd('/') + "/app/ws"`；`Authorization: Bearer <token>` 头。
  - 退避：1/2/4/8/16/30 秒上限，`jitter` 注入；`onNetworkAvailable()` 立即打断等待。
  - 关闭码 4401：`onUnauthorized(token)` 后 `stop()`，不重连；1011/1012/网络错误 → 退避重连。
  - `OkHttpWsConnector`：独立 `OkHttpClient`（`readTimeout(0)`、`pingInterval(25s)`），
    `callbackFlow` 桥接 `WebSocketListener`，关闭时 `cancel()`。

- [ ] **Step 1: 写失败测试**

`AppWsManagerTest`（`runTest` + `StandardTestDispatcher`；`FakeConnector` 按脚本产出 frame，记录 `attempts`）：

```kotlin
@Test fun `connects and applies device report and turn state`()
@Test fun `stale idle with different turn key is ignored`()
@Test fun `backoff follows 1 2 4 seconds and resets on success`()
@Test fun `4401 invalidates token and does not reconnect`()
@Test fun `network available retries immediately`()
@Test fun `stop closes connection and ignores later frames`()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*AppWsManagerTest" --console=plain`
Expected: 编译失败。

- [ ] **Step 3: 实现**

循环骨架：

```kotlin
private fun loop() = scope.launch {
    var attempt = 0
    while (isActive && running) {
        val session = sessionProvider() ?: break
        connection.value = ConnectionState.Connecting
        var received = false
        try {
            connector.connect(wsUrl(session.serverUrl), session.token).collect { frame ->
                when (frame) {
                    is WsFrame.Text -> { received = true; attempt = 0; connection.value = Connected; handle(frame.json) }
                    is WsFrame.Closed -> if (frame.code == 4401) {
                        onUnauthorized(session.token); running = false; return@launch
                    }
                    is WsFrame.Failed -> Unit
                }
            }
        } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        if (!running) break
        val delayMs = jitter(BACKOFF[min(attempt, last)].inWholeMilliseconds)
        attempt++
        connection.value = Retrying(attempt, delayMs)
        withTimeoutOrNull(delayMs) { networkSignal.first() }
    }
}
```

`handle(json)` 解析 `type`：`device.status` / `device.report` / `action.result` / `voice.event` /
`conversation.turn_state` / `remote.ack` / `error`；只把 `v==2` 的已知类型转 `WsEvent`，未知类型忽略。
`turn_state` 先过守卫再改 `turnStates`。所有异常吞掉（日志不得带 payload）。

- [ ] **Step 4: 运行测试通过**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*AppWsManagerTest" --console=plain`
Expected: BUILD SUCCESSFUL。

- [ ] **Step 5: 接入容器与导航**

`AppContainer` 暴露 `appWsManager`；`AppNav`：

```kotlin
LaunchedEffect(current?.token) {
    if (current != null) container.appWsManager.start() else container.appWsManager.stop()
}
```

- [ ] **Step 6: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): add resilient app websocket manager"
```

---

### Task 8: 角色列表 / 编辑页与自建导航栈

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppRoute.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppNav.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/assistants/AssistantsViewModel.kt`、`AssistantsScreen.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/assistants/AssistantEditViewModel.kt`、`AssistantEditScreen.kt`
- Delete: `companion/app/src/main/java/me/rerere/companion/feature/home/HomePlaceholderScreen.kt`
- Create tests: `companion/app/src/test/java/me/rerere/companion/feature/assistants/AssistantsViewModelTest.kt`、
  `AssistantEditViewModelTest.kt`

**Interfaces:**
- Consumes: Task 5 的 `AssistantRepository`、`AssistantDto`。
- Produces:
  - `sealed interface AppRoute { data object Assistants; data class Conversations(assistantId, assistantName); data class Chat(conversationId, assistantId, assistantName); data class Memories(assistantId, assistantName); data class AssistantEdit(assistantId: String?) }`
  - `AssistantsViewModel(repo)`：`StateFlow<AssistantsUiState>`（`loading/items/error/deletingId`）与
    `events: SharedFlow<AssistantsEvent>`（`Edit(id?)`、`Open(assistant)`、`Message(text)`）；
    `refresh()`、`delete(id)`、`logout` 由屏幕回调容器完成。
  - `AssistantEditViewModel(repo, assistantId: String?)`：加载现有角色；字段改动写入 `state`；
    `save()` 校验（name 非空、temperature 0..2、top_p 0..1、context ≥0）；成功后 `Saved` 事件。
  - UI：`AssistantsScreen` 顶部栏（标题、刷新、退出登录），LazyColumn 卡片（首字圆形头像、名称、
    `model_id` 或“默认模型”、搜索模式/记忆 chip），FAB 新建，卡片长按/溢出菜单编辑/删除（删除二次确认）。
  - `AssistantEditScreen`：`OutlinedTextField`（名称、人设、模型 ID、上下文条数、temperature、top_p）、
    `Switch`（角色记忆、全局记忆、动作联动）、“自动总结”行置灰并标注“未实现”、搜索三态
    `SingleChoiceSegmentedButtonRow`（关闭/自动/总是）。保存按钮在保存中禁用。
  - `AppNav`：`remember { mutableStateListOf<AppRoute>(AppRoute.Assistants) }`；`BackHandler` 弹栈；
    登录态变化时重置到 `Assistants`。从角色卡片进入 `Conversations`，从编辑进入 `AssistantEdit`。
  - 加载失败显示 `error` + 重试；空列表显示“还没有角色” + 新建按钮。

- [ ] **Step 1: 写失败测试**

`AssistantsViewModelTest`：初始加载成功/失败；`delete` 成功后列表移除；`delete` 失败保留并提示。
`AssistantEditViewModelTest`：新建保存调用 `create` 且 body 含 `web_search_mode`；非法 temperature
不调用仓库并给出错误；编辑加载映射字段；保存失败展示可读错误。

- [ ] **Step 2: 运行确认失败**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*AssistantsViewModelTest" --tests "*AssistantEditViewModelTest" --console=plain`
Expected: 编译失败。

- [ ] **Step 3: 实现 ViewModel 与页面**

页面只消费 `StateFlow`/`SharedFlow`，不直接调仓库；`onLogout = container::logout`；所有错误来自
`AppError.message`。

- [ ] **Step 4: 运行测试 + 构建**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*AssistantsViewModelTest" --tests "*AssistantEditViewModelTest" assembleDebug --console=plain`
Expected: BUILD SUCCESSFUL。

- [ ] **Step 5: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): add assistant management screens"
```

---

### Task 9: 会话列表 / 聊天页（SSE、取消、重试、语音事件、纠错）/ 记忆页

**Files:**
- Create: `companion/app/src/main/java/me/rerere/companion/feature/chat/ConversationsViewModel.kt`、`ConversationsScreen.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/chat/ChatViewModel.kt`、`ChatScreen.kt`
- Create: `companion/app/src/main/java/me/rerere/companion/feature/memories/MemoriesViewModel.kt`、`MemoriesScreen.kt`
- Modify: `companion/app/src/main/java/me/rerere/companion/ui/navigation/AppNav.kt`
- Create tests: `ConversationsViewModelTest.kt`、`ChatViewModelTest.kt`、`MemoriesViewModelTest.kt`

**Interfaces:**
- Consumes: Task 6 `ChatRepository`/`ChatEvent`、Task 7 `AppWsManager`（`turnStates`、`events`）、
  Task 5 `ConversationRepository`/`MemoryRepository`、Task 3 服务端字段。
- Produces:
  - `ConversationsViewModel(repo, assistantId)`：`loading/items/error`；`refresh/create/delete`；
    `conversation.turn_state` 的 generating 标记由屏幕从 `wsManager.turnStates` 读取。
  - `ChatViewModel(conversationId, conversationRepo, chatRepository, wsManager)`：
    - `StateFlow<ChatUiState>`：`loading/error/title/assistantName/messages/input/sending/generating/
      voiceDraft/voiceInterrupted/correctionTarget/snackbar`
    - `ChatMessageUi(id, role, text, reasoning, interrupted, streaming, failed, nodeId, userMessageId)`
    - `send()`：本地追加 USER + 流式 ASSISTANT，生成 `clientMsgId = UUID.randomUUID().toString()`；
      收集 `ChatEvent`；`Done(replayed=true)` → `reload()`；`Failed` → 标记 `failed=true` 并保留 `clientMsgId`
    - `retry()`：仅对失败消息，复用 `clientMsgId` 重新调用
    - `stop()`：取消收集任务，把当前 ASSISTANT 标 `interrupted=true`、`streaming=false`
    - `correct(text)`：仅当目标 USER 消息有 `userMessageId`；成功后本地改文本并提示
      “回答未更新，可重新发送”
    - 收集 `wsManager.events`：`Voice(asr_final)` 且 conversation 匹配 → `voiceDraft`；
      `Voice(turn_end)` → `voiceInterrupted`；`conversation.turn_state` 由 `turnStates` 驱动 `generating`
    - 只允许在 `!sending && !generating` 时发送；离线时（WS 未连接）不禁止 REST 聊天，但按钮需有
      网络错误可读提示（不静默）。
  - `MemoriesViewModel(repo, assistantId)`：列表/新增/编辑/删除；内容为空禁止保存。
  - UI：会话列表（新建/删除二次确认/生成中角标）、聊天页（消息气泡、思考折叠、语音输入气泡、
    打断标记、失败重试、发送/停止、输入框、纠正入口）、记忆页（列表 + 编辑对话框）。
  - 消息树：按 `node_index` 升序，取 `messages[select_index]`；`USER`/`ASSISTANT` 分别对齐左右。

- [ ] **Step 1: 写失败测试**

`ChatViewModelTest`（假 `ChatRepository` 用 `MutableSharedFlow` 驱动；`MainDispatcherRule` + `runTest`）：

```kotlin
@Test fun `send appends user and streams assistant deltas`()
@Test fun `done sets ids and clears streaming`()
@Test fun `replayed done reloads conversation`()
@Test fun `failed keeps client message id for retry`()
@Test fun `stop cancels stream and marks interrupted`()
@Test fun `correction disabled without user message id`()
@Test fun `correction only sends text and shows stale answer notice`()
@Test fun `turn state generating disables send`()
```

`ConversationsViewModelTest`：列表、新建后刷新、删除二次确认由 UI 层保证（VM 只暴露 delete）。
`MemoriesViewModelTest`：列表/创建/空内容拒绝/删除。

- [ ] **Step 2: 运行确认失败**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest --tests "*ChatViewModelTest" --tests "*ConversationsViewModelTest" --tests "*MemoriesViewModelTest" --console=plain`
Expected: 编译失败。

- [ ] **Step 3: 实现 ViewModel**

聊天状态机：`streamJob` 单实例；`send()` 先 `streamJob?.cancel()` 不允许并发；收集事件时用
`combine` 之外的最简单串行更新；`reload()` 重拉 `conversation.detail` 并重建消息列表；
`clientMsgIdByAssistantTempId` 保存失败消息的重试键。

- [ ] **Step 4: 实现页面**

`ChatScreen` 顶部栏：返回、标题、记忆按钮、生成中 `LinearProgressIndicator`；消息区 `LazyColumn`
（`reverseLayout=false`，发送后 `scrollToItem(last)`）；输入区：语音草稿气泡（“语音输入：…” + 纠正按钮）、
`OutlinedTextField` + 发送/停止按钮；底部 `SnackbarHost`。纠错对话框只允许非空文本。

`ConversationsScreen`：列表项显示标题、更新时间、生成中角标（来自 `turnStates`）、删除按钮（确认框）。

`MemoriesScreen`：列表、FAB 新增、点击编辑、长按删除（确认），保存中禁用。

- [ ] **Step 5: 运行测试 + 构建**

Run: `cd companion/app; .\gradlew.bat testDebugUnitTest assembleDebug assembleRelease --console=plain`
Expected: BUILD SUCCESSFUL。

- [ ] **Step 6: 提交**

```bash
git add companion/app
git commit -m "feat(companion-app): add conversations, streaming chat and memories"
```

---

### Task 10: 迁移日志、README、计划回填与整体审查

**Files:**
- Create: `docs/语音机器人需求文档/v2_migration_log/28_phase4_4b_assistants_chat_ws.md`
- Modify: `docs/语音机器人需求文档/v2_migration_log/README.md`
- Modify: `docs/superpowers/plans/2026-11-27-companion-app-phase4b.md`（勾选 + 偏差表）
- Modify: `docs/superpowers/specs/2026-11-27-companion-app-phase4-design.md`（仅当实现与设计不符）

- [ ] **Step 1: 记录真实命令输出**

日志必须含：改动文件、`pytest` 汇总（数字/耗时）、`testDebugUnitTest` 汇总（suite/例数）、
`assembleDebug/Release` 结果与 APK 字节数、`git status`、偏差表、阻塞、下一步、真机待办。

- [ ] **Step 2: 勾选计划并写偏差**

每完成一个 Task 立即勾选；偏差写“计划→实际→原因→代价”。

- [ ] **Step 3: 全量验证**

Run（仓库根）:
```
… -m pytest -q
cd companion/app; .\gradlew.bat testDebugUnitTest --rerun-tasks --console=plain
cd companion/app; .\gradlew.bat assembleDebug assembleRelease --console=plain
git status --porcelain
```
Expected: pytest 全绿；App 全绿；两个 APK 成功；工作区干净。

- [ ] **Step 4: 独立审查**

派子智能体审查：幂等三种时刻、turn_state 乱序、SSE 分帧、WS 4401/退避、纠错归属、红线 grep
（`TrustManager|HostnameVerifier|printStackTrace|Log\.[dviwe]|apiKey|sk-`）。
修复后重跑 Step 3，再复审直至通过。

- [ ] **Step 5: 提交**

```bash
git add docs
git commit -m "docs(companion): record phase 4 block 4b"
```
