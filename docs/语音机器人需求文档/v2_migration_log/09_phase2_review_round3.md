# 09 · Phase 2 三次审查修复（上报顺序 / 双向终态去重 / 设备回退）

- 日期：2026-11-26
- 状态：✅ 完成（Critical 1 条、Important 2 条全部修复；Minor 尽量关闭）
- 分支：`feature/companion-robot-v2`
- 审查对象：`c8ceb3b9..12393818`（08 提交）
- 审查结论：**With fixes**（核心方向正确，但下线冲刷、终态二次广播、设备回退三项需先修）

## 1. 目标与范围

第三轮独立审查确认 08 的三个核心修复方向正确，同时发现：

| 编号 | 级别 | 问题 | 影响 |
| :--- | :--- | :--- | :--- |
| C1 | Critical | `stop_writer()` 在 writer 可能仍持有同一条上报时并发调用 `_flush_reports()` | 慢的旧上报晚于新上报提交，数据库状态回退 |
| I2 | Important | 设备先回报 `action.result`、服务端随后急停/断连 | 同一 `action_id` 广播两次终态 |
| I3 | Important | 会话级设备撤销后，`resolve_device_for_conversation()` 仍返回该设备 | 遮蔽角色级在线设备，`robot.*` 工具被静默关闭 |

另有 Minor：`shutdown()` 绕过 flush、`4429` 在 uvicorn 下不可达、`EventBus.clear()`
后 `AppGateway` 不再订阅、关键终态测试只断言「存在」而非「恰好一次」。

本轮只修这些，不新增功能，不进入音频链路。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `stop_writer()` 改为「先投递停止哨兵、由 writer 按 FIFO 写完；仅超时才取消并接管」；新增 `_writer_stopping` 防止停止后仍有新上报挤掉哨兵；`shutdown()` 先 flush 再注销/关闭；设备结果被接受时同步 `mark_action_reported()` |
| `companion/server/app/services/action_orchestrator.py` | 新增 `mark_action_reported(device_id, action_id)`：把仍是 `current` 的 job 标记为已回报，阻止后续 `_terminate` 再发终态 |
| `companion/server/app/services/device_registry.py` | 绑定解析改为三段：在线会话级 → 在线角色级 → 已撤销会话级；撤销设备不再遮蔽在线回退设备 |
| `companion/server/app/services/events.py` | 新增 `drain_background_tasks()`；关闭阶段等待后台广播完成，超时后取消 |
| `companion/server/app/services/app_gateway.py` | `_ensure_subscribed()` 不再只信布尔标记，每次连接都幂等 `subscribe`，可恢复 `EventBus.clear()` 后的订阅 |
| `companion/server/app/config.py` | 新增 `WS_TRANSPORT_MAX_MESSAGE_BYTES`（128 KiB） |
| `companion/server/app/main.py` | uvicorn `ws_max_size` 使用传输层上限（高于应用层 64 KiB）；lifespan 关闭时 drain 后台任务 |
| `companion/docs/protocol_v2.md` | 8.4 补充双向终态去重与上报 writer FIFO/关闭冲刷约束 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 09 |

### 测试

| 路径 | 新增/加强覆盖 |
| :--- | :--- |
| `companion/server/tests/test_action_orchestrator.py` | 超时终态断言 `len == 1`；新增「设备已回报后急停不得再发终态」竞态测试 |
| `companion/server/tests/test_device_gateway.py` | 新增慢 handler 下的 flush 顺序测试（`[1, 2]`）；新增 `shutdown()` 冲刷排队上报测试 |
| `companion/server/tests/test_chat_action_loop.py` | 新增「会话设备撤销 + 角色级在线设备」回退测试，断言工具仍注入并真正下发动作 |
| `companion/server/tests/test_app_gateway.py` | 新增 `EventBus.clear()` 后新连接重新订阅并收到广播的测试 |
| `companion/server/tests/test_ws_router.py` | 新增应用层超限帧 `4429 message_too_large` 测试；断言传输层上限高于应用层 |

## 3. 行为变化

### 3.1 上报落库顺序（C1）

- `stop_writer()` 不再自己边 flush 边让 writer 跑。
- 流程：`_writer_stopping = true`（之后 `queue_report` 丢弃新上报）→ 等待把
  `_WRITER_STOP` 放进容量为 1 的队列 → 等 writer 按 FIFO 处理完并退出。
- 只有 writer 超过 1 秒仍未退出时才 `cancel()`，随后由当前协程接管剩余队列，
  保证不会出现两个 handler 并发写同一条设备状态。
- `DeviceGateway.shutdown()` 也走 `stop_writer()`，服务重启不再丢最后一条上报。

### 3.2 终态恰好一次（I2）

- 设备 `action.result` 被 `DeviceSession.resolve_result()` 接受后，网关**同步**调用
  `mark_action_reported()`，把调度器 `current` 中相同 `action_id` 的 job 标记为已广播。
- 因此即使 App 急停或设备恰好断连发生在 worker 恢复之前，`_emit_terminal()` 也会直接返回。
- 两个方向现在都覆盖：

| 顺序 | 结果 |
| :--- | :--- |
| 服务端先结算（超时/急停/断连），设备回执后到 | `resolve_result()` 返回 `None`，丢弃 |
| 设备先回报，服务端随后急停/断连 | `mark_action_reported()` 已标记，调度器不再补发 |

### 3.3 设备绑定回退（I3）

`resolve_device_for_conversation()` 的固定顺序：

1. `is_active=true` 且 `bound_conversation_id=conversation`；
2. `is_active=true` 且 `bound_conversation_id is null` 且 `bound_assistant_id=assistant`；
3. 已撤销的会话级设备（仅用于把原因区分为 `inactive`，而不是 `unbound`）。

会话设备撤销后，只要角色级仍有在线设备，就继续注入并使用 `robot.*` 工具。

### 3.4 其他

- `4429` 重新可达：uvicorn 传输层上限 128 KiB > 应用层 64 KiB。
- 事件总线关闭前排空后台广播任务；`EventBus.clear()` 后新 App 连接能重新订阅。
- 运行测试：`160 passed`（08 基线 153 + 7 个新增/加强用例）。

## 4. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_action_orchestrator.py companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py companion/server/tests/test_ws_router.py companion/server/tests/test_chat_action_loop.py
........................................................................ [ 96%]
...                                                                      [100%]
75 passed in 2.65s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
........................................................................ [ 45%]
........................................................................ [ 90%]
................                                                         [100%]
160 passed in 4.98s
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 停止 writer 采用「哨兵 + 等待」，而不是给 handler 加锁 | 单纯加锁无法阻止 flush 抢先拿到队列中较新的上报；哨兵让 writer 成为唯一消费者才能严格保序 | writer 超过 1 秒仍卡在慢 DB 时会取消并接管，极端慢库下最后一条可能丢失（与旧实现相同） |
| 设备回报标记放在 `DeviceGateway` 而非只在调度器 | 只有网关知道设备回执已被接受；在同一 tick 内同步标记，才能堵住急停/断连窗口 | 依赖 `_SCHEDULERS` 进程内单例；多 worker 部署不适用（当前固定单 worker） |
| 绑定解析第三段只返回撤销的会话级设备 | 与审查建议一致，保留 `inactive` 语义；角色级撤销设备仍按 `unbound` 处理 | 若未来需要区分「角色级设备被撤销」，需再扩一段查询 |
| `shutdown()` 先 flush 再注销链路 | `_handle_report` 会检查「是否仍是当前链路」，先注销会把最后一条上报丢掉 | 服务关闭时多等一次 DB 写入 |
| `ws_max_size` 放宽到 128 KiB | 否则应用层 `4429` 永远是死代码；真正超大帧仍由 uvicorn 以 1009 关闭 | 单帧内存上限提高 64 KiB |

## 6. 未完成项 / 阻塞点

- 每 Token / 每设备连接数上限、连接建立速率限制仍未实现。
- 多进程 / 多 worker 下调度器与事件总线仍是进程内的；当前 systemd 固定单 worker。
- 音频链路（二进制帧、ASR/TTS、半双工、barge-in）、Phase 3 双模拟器、Android App、
  ESP32 固件均未开始。
- 设备先回报后急停的去重依赖同一事件循环内的同步标记；跨进程场景需要持久化或集中协调。

## 7. 下一步

1. 提交本轮修改并推送 `feature/companion-robot-v2`。
2. 进入 Phase 3：`device_simulator`（支持 `--protocol-version 1/2`）+ `app_simulator`
   + 真实 uvicorn/WebSocket 端到端测试（18 动作闭环、双源仲裁、超时强停、撤销断连、
   离线不补发、v1 降级）。
3. 音频链路留到后续音频 Block，模拟器不能代替真机验收。
