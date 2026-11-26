# 12 · Phase 3 审查修复：上报冲刷顺序、`stop_writer()` 并发与模拟器可联调性

- 日期：2026-11-26
- 状态：✅ 完成（全量 186 passed；Phase 3 端到端 15 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`5585c171..f9e506e5`（Phase 3 提交）+ 第五轮审查遗留的 2 条 Important
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

上一轮（日志 11）交付了双模拟器与真实 WebSocket 端到端矩阵，独立审查给出：
Critical 0、Important 3（模拟器 CLI 必崩、音量 LWW 假绿、就绪探针跨线程读私有状态），
以及第五轮遗留的 2 条 Important（撤销/替换丢最后一条上报、并发 `stop_writer()` FIFO 反转）。

本轮要解决三件事：

1. **生产代码**：`device_gateway.py` 的上报冲刷顺序与并发行性。
2. **模拟器**：CLI 主路径可用、断连语义贴近真机、v1 不上报 v2 状态帧、握手严格校验。
3. **测试**：把「看起来在测、其实没测」的用例改成真正有效的断言，并补上两条并发回归。

未改动任何根 RikkaHub Android 工程文件。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `close()` 先冲刷再置 `closed`；`_install()` 替换前冲刷旧链路；`stop_writer()` 加锁串行 |
| `companion/server/tests/test_device_gateway.py` | 新增 3 条回归（撤销冲刷、替换冲刷、并发 `stop_writer()` FIFO）；替换用例等待关闭完成 |
| `companion/simulators/device_simulator.py` | CLI 常驻、先断链后取消动作、v1 不发 v2 上报、`hello_ack` 严格校验、`expression.set` 容错回执、长行折行 |
| `companion/simulators/app_simulator.py` | `close_ws()` 取消并清空等待者 |
| `companion/tests/test_phase3_e2e.py` | LWW 真实验证、黑盒就绪探针、限速确定性、tool 结果回灌、日志密码采集、负向断言屏障、新增 2 例 CLI 回归 |
| `companion/docs/protocol_v2.md` | §4.4 `expression.set` 明确为 fire-and-forget，并规定带 `action_id` 变体必须回执 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 12 |

## 3. 生产代码修复

### 3.1 撤销 / 替换会丢最后一条 `device.report`

原顺序：

```python
async def close(self, code=1000, reason=""):
    self.closed = True          # ← 先置位
    self._stop_writer()         # ← 只是取消 writer，不冲刷
```

`_handle_report()` 的第一行就是 `if session.closed ... : return`，因此撤销设备
（`DELETE /api/devices/{id}` → `link.close(4401)`）和会话替换
（`_install()` → `old.close(4409)`）都会把队列里/在途的最后一条上报丢掉，
重连后设备快照回退到旧值。

修复：

```python
async def close(self, code=1000, reason=""):
    await self.stop_writer()    # 先在仍是 current 链路时按 FIFO 冲刷
    self.closed = True
    self._stop_writer()
```

`_install()` 同理，必须在 `register_device_link()` **之前**冲刷旧 writer：一旦新链路注册，
旧会话的上报会在 `_handle_report()` 的 `get_device_link(...) is not session` 检查处被丢弃。

### 3.2 并发 `stop_writer()` 导致 FIFO 反转

`stop_writer()` 的冲刷分两段：等 writer 退出 → 当前协程用 `_flush_reports()` 接管队列。
若两个调用方并发（`_teardown`、`shutdown()`、`handle()` 异常路径），两份 `_flush_reports()`
会分别取走 in-flight 条目与队列条目，两条 handler 并发写库，慢的旧上报后提交，把新状态覆盖成旧值。

修复：`self._writer_stop_lock = asyncio.Lock()`，`stop_writer()` 只做
`async with self._writer_stop_lock: await self._drain_writer()`，原逻辑整体移入 `_drain_writer()`。

**回归有效性验证**（把生产改动 stash 掉再跑新增用例）：

```text
> git stash push -- companion/server/app/services/device_gateway.py
> pytest -q companion/server/tests/test_device_gateway.py -k "close_flushes or replacement_flushes or concurrent_stop_writer"
E       assert order == [1, 2]
E       assert [2, 1] == [1, 2]
FAILED test_close_flushes_queued_report
FAILED test_replacement_flushes_queued_report
FAILED test_concurrent_stop_writer_preserves_fifo
3 failed, 23 deselected in 1.00s
```

三条用例在旧代码上全部变红、在新代码上全绿，说明它们确实覆盖了上述两个缺陷，
而不是「加了个恒真断言」。

## 4. 模拟器修复

| 问题 | 原行为 | 修复 |
| :--- | :--- | :--- |
| CLI 主路径必崩 | `wait_closed()` 默认 5s 超时，`python -m companion.simulators.device_simulator` 连接 5 秒后抛 `TimeoutError` 退出 1 | `wait_closed(timeout: float \| None = 5.0)`；CLI 传 `timeout=None` 常驻，`finally` 里 `close()` |
| 断连语义不真实 | `close()` 先取消动作，任务借仍在的 `ws` 补发 `action.result(failed, stopped)`，覆盖了真机掉电应有的 `device_offline` | 先关 `ws` 再取消动作；优雅停止仍走显式 `action.stop` |
| v1 设备发 v2 上报 | `send_report()` / `_send_result()` 硬编码 `"v": 2`，v1 连接后也主动上报 | v1 直接跳过上报；`v` 取 `self.protocol_version` |
| 握手不校验 | 收到任意 JSON 都当成成功 | 必须是 `type == "hello_ack"`，否则置 `hello_ack=None` 并关闭 |
| `expression.set` 沉默 | 带 `action_id` 时提前 return，调用方只能等超时 | 执行后若带 `action_id` 回 `action.result(done)`；`protocol_v2.md` §4.4 同步声明为 fire-and-forget |
| App 监听器泄漏 | `close_ws()` 不清 `_listeners`，重连后旧 future 可能被新事件唤醒 | 取消并清空所有等待者 |

## 5. 测试加固（全部走真实 TCP/WS）

| 用例 | 原问题 | 现在怎么测 |
| :--- | :--- | :--- |
| `test_volume_last_write_wins_and_status_snapshot` | 两条 `volume.set` 串行 await，第一条在第二条入队前已 `sent`，覆盖分支根本没触发；还接受 `failed` | 先用 `auto_complete=False` 的 `action.play` 占住 worker，再不带等待连发两条 `volume.set`；断言第一条 `failed/superseded`、设备从未收到 `volume.set`、第二条 `done` 且音量为 65；查询快照前先等设备上报 |
| `test_v1_device_degrades_and_rejects_remote_commands` | `sleep(0.1)` 证明「没收到」 | 优雅关闭设备连接作屏障（关闭帧之前服务端写下的帧必然已被读入），再断言 `device.received == []` |
| `test_offline_device_never_replays_commands` | `sleep(0.3)` 证明「没补发」 | 以 App 收到 `device.status(online=true)` 为屏障（该广播在 `_install()` 完成后才发），再断言设备历史为空 |
| `test_app_remote_rate_limit` | 依赖 15 次串行 RTT 挤进 1 秒窗口 | `monkeypatch` 窗口为 3 条/秒，发 6 条，断言恰好 3 条 `rate_limited` |
| `test_chat_tool_loop_drives_real_device` | 只间接证明发生了第二轮调用 | 断言 `len(fake.calls) == 2`，且第二轮 `messages` 里带 `tool_call_id == "call_1"` 的 tool 消息、`ok is True` |
| `test_logs_do_not_leak_tokens_or_passwords` | 登录发生在 setup，caplog 在 call 阶段才安装，密码断言恒真；按 logger 名前缀过滤会连带放过服务端 `websockets.server` | 在 call 阶段调用 `login_other_app()`；把 `httpx/httpcore/websockets` 的**级别**压到 WARNING（而非过滤 record），断言范围改为 `companion.*` + `uvicorn` |
| `connect_app()` | 跨线程轮询 `app_gateway._sessions`（活 `set` 迭代可能 `RuntimeError`，且远端服务器不可用） | 发一条指向不存在设备的 `remote.get_status`，等 `remote.ack(ok=false, error=device_offline)`：黑盒、线程安全、远端可用 |
| `test_device_simulator_cli_stays_connected`（新增） | 无回归保护 | 子进程跑真实 CLI，等 6s（超过旧默认超时）后断言进程存活，并下发 `volume.set` 验证它真的握上手并能收指令 |
| `test_device_wait_closed_without_timeout_blocks_until_close`（新增） | 无 | `wait_closed(timeout=None)` 在 0.1s 内不应返回，`close()` 后 5s 内返回 |

CLI 回归同样验证过「在旧代码上必红」：

```text
> git stash push -- companion/simulators/device_simulator.py
> pytest -q companion/tests/test_phase3_e2e.py -k cli
E           assert 1 is None
E            +  where 1 = poll()
FAILED test_device_simulator_cli_stays_connected
1 failed, 14 deselected in 7.96s
```

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py -k cli
1 passed, 14 deselected in 7.86s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
15 passed in 11.82s
15 passed in 11.75s
15 passed in 11.76s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
26 passed in 1.09s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
186 passed in 16.05s
186 passed in 16.16s
186 passed in 16.01s
```

## 7. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| `close()` 无条件先 `stop_writer()` | 撤销/替换是外部路径，`_handle_report()` 只看 `closed` 标记，先置位必然丢最后一条 | 关闭会多等一次冲刷（上限即 writer 的两个超时常量）；测试无感知 |
| `_install()` 在注册新链路**之前**冲刷旧链路 | `_handle_report()` 同时检查 `get_device_link(...) is session` | 替换路径多一次 DB 写入等待；设备侧 4409 关闭略晚 |
| `stop_writer()` 用 `asyncio.Lock` 全串行 | 只加幂等短路挡不住「两个调用方都进入冲刷」 | 极端情况下一个调用方等另一个的冲刷超时（与串行队列语义一致） |
| 就绪探针使用不存在的 `device_id` | 协议没有 App 侧 `hello/hello_ack`，只能靠业务 ack 判断注册 | 若将来 `get_status` 对未知设备改成静默成功，探针需换命令 |
| 限速用例 `monkeypatch` 常量而不是伪造时钟 | 只改窗口大小，仍走真实 `allow_rate()` 与真实 WS | 与生产窗口 10/s 不同；真实窗口行为另由 `test_app_gateway.py` 覆盖 |
| 日志脱敏改为压第三方库**级别** | 按 logger 名过滤会把服务端 `websockets.server` 一起放过；压级别只作用于本进程测试 | 若客户端库改用项目自有 logger 名，可能重新进入断言范围（会误报，不会漏报） |
| 模拟器 `close()` 先断链 | 真机掉电/断网就是这么表现；原实现反而掩盖 `device_offline` 路径 | 需要「优雅停止」的用例必须显式发 `action.stop`（现有用例已如此） |
| `expression.set` 为 fire-and-forget，但模拟器对带 `action_id` 变体回执 | 协议 §4.4 示例无 `action_id`，服务端 `REMOTE_TYPES` 也未开放；固件照抄模拟器时不能沉默 | 若后续把该指令纳入 `remote.*` 并带 `action_id`，服务端需补对应 ack 字段 |

## 8. 未完成项 / 阻塞点

- **未修 Minor（有意保留）**：模拟器没有实现设备侧 `>10 次/秒` 拒绝、`max_duration_ms` 硬超时
  看门狗、越界角度「截断而非拒绝」。理由：这三条属于固件行为，服务端在上游已校验，
  本轮模拟器不作为固件验收依据；等音频/固件 Block 一起对齐协议再补。
- **未补的端到端覆盖**：`servo.move`、`idle.set`、校准 preview/save、4409 替换、4408 握手超时/非法首帧、
  重复 `action.result`。审查者用一次性探针在真实 WS 上验证过行为正确，因此属回归覆盖缺口而非缺陷；
  其中 4409/4408 已由 `test_device_gateway.py` 的 FakeChannel 覆盖。
- **协议歧义**：上行 `device.report` / `action.result` 是否应带 `session_id` / `turn_id`，
  §2 与 §5 示例不一致，待硬件 Block 定稿。
- **错误体风格**：仓库既有 `detail` 为中文可读文案，与 `protocol_v2.md` §11.6「`detail` 为 code」不一致，
  未在本轮统一（涉及全部路由，单独评估）。
- **单 worker 限制**：`_SCHEDULERS`、`EventBus`、`pending` 仍是进程内状态；连接数上限与建连速率限制未实现。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `f9e506e5..HEAD`。
3. 进入音频 Block（二进制帧语义、ASR/TTS、半双工、barge-in）；音频验收必须真机，模拟器不算数。
