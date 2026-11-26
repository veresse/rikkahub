# 11 · Phase 3：设备 / App 双模拟器与真实 WebSocket 端到端验收

- 日期：2026-11-26
- 状态：✅ 完成（13 个端到端用例；全量 181 passed）
- 分支：`feature/companion-robot-v2`
- 范围：`docs/语音机器人需求文档/v1_v2.md` Phase 3（3.3 与第 209 行起）
- 未涉及：音频帧/ASR/TTS/barge-in、Android App、ESP32 固件

## 1. 目标与范围

Phase 3 要回答的问题是：**脱离 FakeChannel 之后，真实 TCP/WebSocket 上的协议是否仍然闭环。**

本轮交付：

1. `companion/simulators/device_simulator.py`：按 `protocol_v2.md` 实现设备侧的握手、
   动作执行、急停、姿态/音量状态与上报，可单机运行也可被 pytest 驱动。
2. `companion/simulators/app_simulator.py`：管理员登录、REST、`/app/ws` 遥控与事件收集。
3. `companion/tests/test_phase3_e2e.py`：在后台线程启动**真实 uvicorn**，用两个模拟器跑
   真实 `127.0.0.1` TCP/WebSocket；LLM 用进程内脚本化 `DeepSeekClient`，不需要任何云密钥。

明确不做：模拟器不伪造音频链路，也不作为真机验收依据。

## 2. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/simulators/__init__.py` | 导出 `SimulatedApp` / `SimulatedDevice` |
| `companion/simulators/device_simulator.py` | 设备侧协议实现；`--protocol-version 1/2`、`--time-scale`、`--report-interval` |
| `companion/simulators/app_simulator.py` | App 侧 REST + `/app/ws`；`remote()` 等待同 `request_id` 的 `remote.ack`；SSE 收集 |
| `companion/tests/test_phase3_e2e.py` | 真实 uvicorn/WS 端到端矩阵（13 例） |
| `companion/server/tests/test_conversations_api.py` | 新建会话 REST 回归（见 §3.1） |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/routers/conversations.py` | 修复 `create_conversation()` 的嵌套事务（见 §3.1） |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 11 |

## 3. 行为变化与缺陷修复

### 3.1 端到端测试暴露的真实缺陷：新建会话必然 500

`POST /api/conversations` 原实现：

```python
assistant = await db.get(AssistantModel, data.assistant_id)  # 自动开启事务
...
async with db.begin():                                       # 这里抛 InvalidRequestError
```

SQLAlchemy `AsyncSession` 在第一条语句后自动 `begin()`，再显式 `begin()` 会抛
`A transaction is already begun on this Session`。之前所有测试都直接写库或走
`FakeChannel`，没有调用该 REST 接口，因此 166 个用例全绿也未发现。

修复：把 `db.get()` 移进 `async with db.begin()`，先开事务再查询；角色不存在时在
事务内抛 `HTTPException` 并回滚。新增 `test_conversations_api.py` 覆盖成功路径与
`404` 悬空角色路径。

### 3.2 模拟器关键行为

| 场景 | 模拟器行为 |
| :--- | :--- |
| 握手 | 发送 `hello`（`v`/`device_id`/`capabilities`/`audio`），等待 `hello_ack`，随后主动上报一次 |
| v1 设备 | `hello.v=1`、`hello_ack.protocol_version=1`、能力空集；只保持音频通道，不处理 v2 控制 |
| `action.play` | 用 `catalog.estimate_duration_ms()` 计算时长（`time_scale` 缩放），结束后落到动作末帧姿态并联动表情 |
| `action.stop` | 回中、取消所有在途动作，被取消的动作回 `action.result(failed, stopped)` |
| `servo.move` / `volume.set` / `idle.set` / 校准 | 做范围与类型校验，非法参数回 `failed` + `invalid_arguments` |
| 上报 | 连接、动作完成、音量/校准变化后主动 `device.report` |
| 离线 | 直接关闭连接；服务端不得在重连后补发历史指令 |

### 3.3 测试必须先确认 App 会话已注册

`websockets.connect()` 在收到 `101` 后即返回，而服务端可能还没执行完
`AppGateway.handle()`；事件广播只投递给已注册会话且**不补发**。因此：

- `E2EContext.connect_app()` 在建立连接后轮询服务端 `app_gateway._sessions`，
  确认会话挂载再继续（仅用于同步，所有断言仍走真实 WS）。
- 该等待暴露的是一致性问题而非测试技巧：真实 App 若刚连上就错过事件，也应该靠
  自己的状态拉取（`remote.get_status`）恢复，协议不承诺补发。

### 3.4 日志脱敏断言的边界

日志测试排除 `httpx` / `httpcore` / `websockets` 客户端 logger：它们在 DEBUG 级别会
打印**自己发出的**请求头（含 `Authorization`），那是模拟器本地日志，不是服务端泄漏。
断言对象是服务端与数据库 logger，并要求至少存在一条 `companion.server` 记录（用急停
路径触发调度器 INFO 日志），避免空集合让测试失去意义。

## 4. 测试矩阵（13 例，全部真实 TCP/WS）

| # | 用例 | 断言要点 |
| :--- | :--- | :--- |
| 1 | `test_device_v2_handshake_contract` | `hello_ack` 的版本、能力、`limits`（pan/tilt/时长/限速）、`warnings` 与 `session_id`；App 收到初始 `device.report(volume=70, idle)` |
| 2 | `test_v1_device_degrades_and_rejects_remote_commands` | v1 握手能力为空；`remote.action.play` 返回 `unsupported_protocol`；v1 设备收不到 v2 控制帧 |
| 3 | `test_all_18_actions_closed_loop` | 18 个动作逐个下发→执行→恰好一条 `done` 终态；设备收到的顺序与动作库一致；抽查末帧姿态 |
| 4 | `test_volume_last_write_wins_and_status_snapshot` | `volume.set` 后到覆盖；每个 `action_id` 恰好一条终态；`remote.get_status` 返回 `source=server` 快照 |
| 5 | `test_action_play_is_fifo` | 两条 `action.play` 按序执行，终态各一条 |
| 6 | `test_action_stop_preempts_queue_e2e` | 急停后队列内与执行中的动作都回 `failed/stopped`，各一条终态；设备收到 `reason=user_stop` 并回中 |
| 7 | `test_execution_timeout_forces_server_stop` | 缩短超时预算后服务端广播 `failed/timeout` 且只一条；设备收到 `reason=server_cancel` |
| 8 | `test_offline_device_never_replays_commands` | 收到 `device.status(online=false)`；离线指令回 `device_offline` 且无 `action_id`；重连后设备历史为空 |
| 9 | `test_app_token_revocation_closes_ws` | 撤销后 App 连接以 `4401 token_revoked` 关闭 |
| 10 | `test_device_revocation_closes_device_ws` | 删除设备后设备连接 `4401 device_revoked`；旧 Token 重连同样 `4401` |
| 11 | `test_chat_tool_loop_drives_real_device` | 首轮注入 `robot.*` 工具；`robot.action` 结果回传模型；SSE 含两段文本与 `tool_result`；设备收到 `source=llm` + `turn_id` 的 `nod` |
| 12 | `test_app_remote_rate_limit` | 15 条快速指令触发 `rate_limited`，被限速的 ack 均 `ok=false` |
| 13 | `test_logs_do_not_leak_tokens_or_passwords` | 服务端日志不含 App Token / 设备 Token / 管理员密码 |

## 5. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
.............                                                            [100%]
13 passed in 5.87s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
.............                                                            [100%]
13 passed in 5.88s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_conversations_api.py
..                                                                       [100%]
2 passed in 0.36s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
........................................................................ [ 39%]
........................................................................ [ 79%]
.....................................                                    [100%]
181 passed in 10.11s
```

环境修复（非代码改动）：本机 venv 的 `httpcore` 曾只剩命名空间目录（缺 `__init__.py`），
导致 `httpx.AsyncClient` 构造即失败。用
`uv pip install --python <venv>\Scripts\python.exe --reinstall httpcore==1.0.9`
重装后恢复；该问题只影响本机临时环境，未写入仓库。

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 用进程内 `uvicorn.Server` + 独立线程启动真实服务 | 保持真实 TCP/WS，同时让 pytest 能访问同一进程的 `app_gateway` 做就绪同步 | 服务的日志/事件循环与测试共享进程，需避免全局状态串扰；`live_server` 用 module scope 隔离 |
| App 就绪同步读 `app_gateway._sessions` | 协议没有 App 侧 `hello/hello_ack`，没有合法的应用层握手可等 | 若未来把模拟器指向**远端**服务器，该检查不可用；此时应改用一次 `remote.get_status` 探活 |
| 日志脱敏排除客户端库 logger | `websockets` 客户端 DEBUG 会打印自己发出的 `Authorization` | 若服务端把 Token 交给第三方库打印，仍会被捕获（未被排除的 logger 都在断言范围内） |
| 超时用例 monkeypatch `_command_timeout_ms` | 真等 3–5 秒会拖慢测试且不稳定 | 只改变预算常量，仍走真实调度器、真实 `action.stop` 下发 |
| 模拟器不实现音频 | 避免用模拟器冒充真机/音频验收 | 音频链路留到后续 Block，必须真机验证 |

## 7. 未完成项 / 阻塞点

- 音频链路（二进制帧语义、ASR/TTS、半双工、barge-in）未开始，`SimulatedDevice` 会忽略二进制下行帧。
- Android App、ESP32 固件未开始；Phase 3 只验证服务端协议闭环。
- 多进程 / 多 worker 下 `_SCHEDULERS`、`EventBus`、`pending` 仍是进程内状态；当前固定单 worker。
- 设备连接数上限、连接建立速率限制仍未实现。
- 第五轮审查（范围 `a76ea35c..5585c171`）返回 2 条 Important：
  1. 外部 `close()`（设备撤销 / 会话替换）先置 `closed` 再停 writer，最后一条 `device.report` 不落库；
  2. 并发 `stop_writer()` 可导致 FIFO 反转。
  两项均属 Phase 2 已提交代码，安排在本轮提交后单独修复并补测试（日志 12）。

## 8. 下一步

1. 提交 Phase 3（模拟器 + 端到端测试 + 会话接口修复）并推送。
2. 派独立子智能体审查本轮结果。
3. 修复第五轮审查的 2 条 Important（`close()` 冲刷顺序、`stop_writer()` 并发），补回归测试。
4. 之后才进入音频 Block；音频验收必须真机。
