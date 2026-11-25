# 04 · Block C（Phase 2 核心）：动作编排与 DeepSeek 工具调用循环

- 日期：2026-11-25
- 状态：✅ 完成（服务端控制面）
- 分支：`feature/companion-robot-v2`
- 前置决策：本轮只做服务端、先控制面；设备在线状态暂由内存链路注册表提供，Block D 由 `DeviceGateway` 接管。

## 1. 目标与范围

1. 新增 `ActionOrchestrator`：把 DeepSeek 的 `robot.*` 工具调用转成 v2 设备指令，负责参数校验、单轮限次、
   每设备排队、限速、覆盖与急停。
2. 新增流式 tool-call 聚合：把 OpenAI/DeepSeek 拆成多个增量的 `tool_calls` 合并成完整调用。
3. 改造 `ChatOrchestrator`：按设备在线状态动态注入 5 个工具，执行工具并把结构化结果回填给模型续说。
4. 保持 v1 既有能力不回退：历史分支、阶梯滞回上下文、搜索、落库、单会话锁。
5. 工具定义中的动作清单从 `companion/shared/actions.json` 动态生成，杜绝三端漂移。

## 2. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/tool_calls.py` | `ToolCallAccumulator`：按 `index` 聚合流式 tool call 增量 |
| `companion/server/app/services/action_tools.py` | 5 个 `robot.*` 工具 Schema、动态注入条件、离线/未绑定提示 |
| `companion/server/app/services/device_registry.py` | 在线设备链路注册表 + 会话/角色设备解析（Block D 接入点） |
| `companion/server/app/services/action_orchestrator.py` | 动作编排：校验、限次、排队、限速、覆盖、急停、超时 |
| `companion/server/tests/test_tool_calls.py` | 增量聚合（拆分参数、乱序多调用、重复 name 帧、空调用） |
| `companion/server/tests/test_action_tools.py` | 工具 Schema 枚举/范围/必填项、注入条件 |
| `companion/server/tests/test_action_orchestrator.py` | 指令构造、参数越界、单轮 3 次、队列 8、10 次/秒、覆盖、急停 |
| `companion/server/tests/test_chat_action_loop.py` | 对话全链路：在线执行/离线/未绑定/关闭开关/v1 设备/超限/坏 JSON/回合上限 |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/orchestrator.py` | 工具循环、设备解析、动态注入、离线提示、结构化 `tool_result` 事件；修复与 `db.begin()` 的事务冲突 |
| `companion/server/app/services/deepseek_client.py` | 修正 `tool_call` 增量产出的文档说明；透传真实 `finish_reason` |
| `companion/server/app/routers/assistants.py` | 角色 CRUD 暴露并写入 `enable_action_sync` |
| `companion/server/app/importer/parser.py` | 导入 `enableActionSync`（v1 备份缺省为 `True`） |
| `companion/server/app/importer/service.py` | 更新角色时同步 `enable_action_sync` |
| `companion/server/app/routers/import_export.py` | 导出写回 `enableActionSync`，保证往返一致 |
| `companion/server/tests/test_full_system.py` | 断言 v1 备份导入后动作联动默认开启 |
| `companion/docs/protocol_v2.md` | 新增 8.3 节：对话 SSE 事件（含 `tool_result`） |

## 3. 关键实现说明

### 3.1 动态注入（严格按 v1_v2.md 2.3）

同时满足以下条件才注入 5 个工具：

1. 角色 `enable_action_sync = True`；
2. 会话/角色绑定的设备存在且在线（`DeviceLink` 已注册）；
3. 设备 `protocol_version >= 2`；
4. `capabilities_json` 含 `"action"`。

不注入时：

- 设备已绑定但离线 → 系统上下文追加「当前实体机器人离线……」；
- 从未绑定设备 → 系统上下文追加「当前没有绑定实体机器人……」；
- 角色关闭动作联动 → 不加提示、不注入工具。

### 3.2 五个工具

| 工具 | 校验 |
| :--- | :--- |
| `robot.action(name,intensity=2,speed=2,repeat=1)` | 动作名必须来自 18 个动作枚举；`intensity/speed ∈ 1..3`；`repeat ∈ 1..5`；动作不支持某档位时只允许默认值（由 `catalog.resolve_params` 判定） |
| `robot.move_head(pan,tilt,speed=500)` | `pan ∈ [-90,90]`、`tilt ∈ [-20,45]`、`speed ∈ [0,1000]` |
| `robot.set_volume(volume)` | `volume ∈ [0,100]`，布尔值不算整数 |
| `robot.get_status()` | 只读，不消耗单轮动作次数 |
| `robot.set_idle(enabled)` | 必须为布尔值 |

### 3.3 执行与排队

- 单轮动作类调用（`robot.action` / `robot.move_head`）上限默认 **3 次**，第 4 次返回
  `{"ok": false, "error": "action_limit_exceeded"}`，绝不下发设备。
- 每设备一个串行调度器：
  - `action.play` FIFO；
  - 队列上限 **8** 条，超出立即 `queue_full`；
  - 全局速率 **10 条/秒**，超出 `rate_limited`；
  - `volume.set` / `servo.move` 为「最后写入胜出」：排队中的同类旧指令返回 `superseded`；
  - 急停 `action.stop` 立即清空队列并中断在途任务（结果标记 `stopped`）。
- 超时：动作按 `max_duration_ms + 1500ms` 等待（`dance` 最长 5000+1500ms），超时向模型返回 `timeout`。
- 设备离线：立即 `device_offline`，不缓存、不补发。

### 3.4 工具循环

- `MAX_TOOL_ROUNDS = 3`：最多 3 轮真实执行；第 4 轮起返回 `round_limit_exceeded`，并去掉
  `tools` 再请求一次纯文字收尾，避免死循环。
- 每轮工具结果以 `role=tool` + `tool_call_id` 回填，模型可据此续说（如点头后「嗯嗯，收到」）。
- SSE 新增 `tool_result` 事件，正文 `content` 与动作下发并行推送；协议见 `protocol_v2.md` 8.3。
- 助手消息落库文本为各轮正文按序拼接，与 App 实际看到的流一致。

### 3.5 修复的事务冲突

`build_context` / 设备解析会触发 SQLAlchemy 自动事务；原 `async with db.begin()` 在工具循环改造后
抛出「a transaction is already begun」。已改为显式 `db.commit()`，由集成测试覆盖。

## 4. 实际执行的命令与真实输出

### 4.1 Block C 新增测试

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q \
    companion/server/tests/test_tool_calls.py \
    companion/server/tests/test_action_tools.py \
    companion/server/tests/test_action_orchestrator.py \
    companion/server/tests/test_chat_action_loop.py
.............................                                            [100%]
29 passed in 1.42s
```

### 4.2 全量测试

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
........................................................................ [ 77%]
.....................                                                    [100%]
93 passed in 6.13s
```

### 4.3 动作库校验

```text
$ & "...\companion-venv\Scripts\python.exe" -m companion.shared.validate_actions
OK: actions=18 version=2.0.0 default_max=3000ms hard_max=5000ms
```

### 4.4 过程中出现过的真实失败

| 失败 | 原因 | 处理 |
| :--- | :--- | :--- |
| 首轮 7 failed：`FOREIGN KEY constraint failed` | `DeviceModel` 无 ORM relationship，插入顺序早于会话 | 测试 seeding 中先 `flush()` 角色/会话再插设备 |
| `a transaction is already begun on this Session` | `build_context` 查询已自动开启事务，落库处仍用 `db.begin()` | 改为显式 `commit()` |
| `SyntaxError`（工具循环分支用了 `elif` 且缺少导入） | 编辑引入的结构错误 | 修正为 `else: if ...` |

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 动作超时只向模型返回 `timeout`，暂不由服务端强制下发 `action.stop` | 强制停止需要与 Block D 的 WS 在途请求/回执统一协调，避免同一链路并发写帧 | Phase 3「动作超时强停」需在 Block D 补上，期间真机可能把超时动作跑完 |
| `robot.get_status` 通过 `DeviceLink.get_status()` 获取 | Block C 只定义链路抽象，设备快照维护属于 `DeviceGateway` | Block D 需实现「实时查询 + 最近上报兜底」 |
| 等待动作回执后才进入下一轮 LLM | 模型需要结果才能续说；上一轮正文已流式下发，不阻塞 TTS 分句 | 若设备很慢，续说会延后（有 3–6.5 秒超时兜底） |
| 多轮正文拼接为一条助手消息 | 与 App 实际看到的 `content` 流一致，避免「看到的」与「存下的」不一致 | 若模型每轮都输出过渡语，消息会偏长 |
| `name` 增量采用「重复整帧忽略」策略 | 个别 Provider 每帧重发完整函数名，直接拼接会产生 `robot.actionrobot.action` | 理论上会吞掉与当前片段完全相同的合法分片（实际不会出现） |
| v1 备份无 `enableActionSync` 时默认 `True` | v2 期望「导入即可用动作联动」；允许角色级关闭 | 用户不想让机器人动时需在 App 里手动关闭 |

## 6. 未完成项 / 阻塞点

- `/device/ws`、`/app/ws` 未实现；`DeviceLink` 目前只有内存注册表与测试桩（Block D）。
- 动作超时强停、设备重连后的调度器清理需在 Block D 与 `DeviceGateway` 一起完成。
- 音频链路（ASR/TTS/Opus/状态机）未实现；「边说边动」目前保证的是对话流不被动作阻塞。
- 错误响应仍为 `{"detail": "<code>"}`，`message` 字段与协议 11.6 的差异留待 App 接入时统一。
- Android App 与 ESP32 固件不在本轮范围。

## 7. 下一步

进入 **Block D：`DeviceGateway`（`/device/ws`，v1/v2 协议、心跳、上线/离线、调度器接入）与
`AppGateway`（`/app/ws`，遥控转发与事件广播）**。届时把 `device_registry` 的内存链路替换为真实
WebSocket 链路，并补齐 `action.stop` 抢占与超时强停。
