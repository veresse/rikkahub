# 10 · Phase 2 四次审查修复（反向窗口 / 在途上报 / 回归测试有效性）

- 日期：2026-11-26
- 状态：✅ 完成（3 条 Important 全部修复；2 条 Minor 顺手关闭）
- 分支：`feature/companion-robot-v2`
- 审查对象：`12393818..a76ea35c`（09 提交）
- 审查结论：**With fixes**（Critical 0，Important 3）

## 1. 目标与范围

第四轮独立审查确认 09 的三项修复方向正确，同时指出：

| 编号 | 级别 | 问题 | 影响 |
| :--- | :--- | :--- | :--- |
| I1 | Important | 服务端先急停、设备回执在 `action.stop` 的 `send_json` 期间到达 | 同一 `action_id` 仍会广播 `stopped` + `done` 两条终态 |
| I2 | Important | `stop_writer()` 超时取消 writer 时，已出队但未写完的 in-flight 上报丢失 | 慢 DB（SQLite `busy_timeout=15s`）下最后一条状态丢失 |
| I3 | Important | 09 新增的慢 flush 测试在旧实现上也能通过 | C1 的回归测试没有区分力 |

另有 Minor：角色级撤销设备退化为 `unbound`、二进制帧无应用层上限、
`WS_TRANSPORT_MAX_MESSAGE_BYTES` 只靠测试约束、关闭阶段 drain 顺序、teardown/shutdown 并发。

本轮只修这些，不新增功能。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/action_orchestrator.py` | `mark_action_reported()` 改为三态（`True` 首次标记 / `False` 已结算 / `None` 无调度器）；`_emit_terminal()` 结算时同步调用 `link.settle_pending()` |
| `companion/server/app/services/device_gateway.py` | `_on_message` 仅在 `mark_action_reported() is not False` 时广播设备回执；`DeviceSession` 新增 `settle_pending()`；writer 新增 `_inflight` 跟踪与 `_run_report_handler()`，取消后由 `_flush_reports()` 补写；停止预算改为常量；`handle()` 异常路径先 flush 再 close；`shutdown()` 与 `_teardown` 用同一把设备锁串行 |
| `companion/server/app/services/device_registry.py` | 增加「已撤销角色级」第四段回退，`inactive` 不再退化为 `unbound`；docstring 澄清 `is_active` ≠ 在线 |
| `companion/server/app/routers/ws.py` | 二进制帧也执行 `WS_MAX_MESSAGE_BYTES` 限制并返回 `4429` |
| `companion/server/app/config.py` | `model_validator` 强制 `WS_TRANSPORT_MAX_MESSAGE_BYTES > WS_MAX_MESSAGE_BYTES` |
| `companion/server/app/main.py` | 关闭顺序改为 `device_gateway.shutdown()` → `drain_background_tasks()` → `app_gateway.shutdown()`（`finally` 保证 App 通道一定关闭） |
| `companion/docs/protocol_v2.md` | 8.4 补充：服务端结算时同步终止设备侧 pending，迟到回执即使调度器已移除也不会广播 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 10 |

### 测试

| 路径 | 新增/加强覆盖 |
| :--- | :--- |
| `companion/server/tests/test_device_gateway.py` | 慢 flush 测试改为「sleep 后提交」，旧实现会得到 `[2,1]`；新增 writer 取消后补写 in-flight 上报；新增 `action.stop` 发送阻塞期间设备回执不重复广播；新增调度器 invalidate 后迟到回执不重复广播 |
| `companion/server/tests/test_action_orchestrator.py` | 新增 `mark_action_reported()` 三态断言 |
| `companion/server/tests/test_chat_action_loop.py` | 新增「仅角色级设备被撤销」得到 `inactive` 提示 |
| `companion/server/tests/test_ws_router.py` | 新增二进制超限帧 `4429`；配置不变量用 `Settings` + `ValidationError` 验证 |
| `companion/server/tests/test_app_gateway.py` | 清理 fixture 改为 async，等待被 cancel 的 worker/link.send 结束，消除跨测试悬挂告警 |

## 3. 行为变化

### 3.1 反向终态窗口（I1）

设备回执到达时，网关按 `mark_action_reported()` 的返回值决定是否广播：

| 返回值 | 含义 | 网关行为 |
| :--- | :--- | :--- |
| `True` | 本次首次标记，服务端尚未广播 | 广播设备回执 |
| `False` | job 已被服务端终态标记 | 丢弃，不再广播 |
| `None` | 没有匹配调度器（直接调用 `DeviceSession.send` 的测试/内部路径） | 保持原行为广播 |

同时 `_emit_terminal()` 在服务端结算时调用 `DeviceSession.settle_pending()`，即使调度器随后被
`invalidate_device_scheduler()` 移除，迟到的 `action.result` 也无法从 `_pending` 中再次冒出。

### 3.2 在途上报（I2）

- `_writer_loop` 在调用 handler 前记录 `_inflight`；正常完成或普通异常后清空。
- 被取消时保留 `_inflight`，`_flush_reports()` 先补写它，再处理队列剩余条目。
- 上报是绝对快照，重试幂等；极端情况下最多重复一次 `device.report` 广播。
- `stop_writer()` 的队列等待与 drain 预算提取为模块常量，便于测试与后续调整。

### 3.3 回归测试有效性（I3）

慢 flush 测试的提交记录移到 `sleep` 之后：旧实现（flush 与 writer 并发）会先提交 `volume=2`、
再提交 `volume=1`，得到 `[2, 1]`；新实现为 `[1, 2]`。

### 3.4 其他

- 角色级设备被撤销且无其它可用设备时，返回该设备并给出 `inactive`，不再误报 `unbound`。
- 二进制帧与文本帧一样受 64 KiB 应用层上限约束，超限统一 `4429 message_too_large`。
- `Settings` 启动时校验传输层上限严格大于应用层上限，避免环境变量把 `4429` 打回死代码。
- 关闭阶段先送设备终态、再关 App 通道；设备锁保证 `_teardown` 与 `shutdown` 不互相丢上报。

## 4. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_action_orchestrator.py companion/server/tests/test_ws_router.py companion/server/tests/test_chat_action_loop.py companion/server/tests/test_app_gateway.py
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 2.52s
```

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
........................................................................ [ 43%]
........................................................................ [ 86%]
......................                                                   [100%]
166 passed in 5.36s
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 设备回执是否广播由 `mark_action_reported()` 三态决定 | 网关需要知道「服务端是否已经结算」，而不是只看设备 future 是否还在 | `None`（无调度器）仍广播，保留直接调用 `DeviceSession.send` 的测试/内部路径 |
| 服务端结算时同步 `settle_pending()` | 仅靠 `emitted` 标记无法覆盖「调度器已被 invalidate 移除」的窗口 | 设备侧 future 提前完成，worker 以 `emit=False` 结束，不再等待设备回执 |
| writer 取消后补写 `_inflight` | 取消可能发生在 handler 已提交但未返回、或未提交之间 | 极端慢 DB 下最多重复一次 `device.report` 广播；上报是快照，幂等 |
| 角色级撤销设备也返回 `inactive` | 与需求「只有完全无可用设备时才返回 inactive」一致，避免误导模型 | 若产品更希望角色级撤销显示 `unbound`，需回退第四段查询 |
| 关闭顺序为设备 → drain → App | 设备下线终态需要 App 通道仍在 | App 关闭稍晚，服务停止时间略增 |
| `Settings` 用 validator 强制上限关系 | 防止环境变量把应用层 `4429` 变成不可达代码 | 配置错误时服务直接启动失败（预期行为） |

## 6. 未完成项 / 阻塞点

- 多进程 / 多 worker 下 `_SCHEDULERS`、`EventBus`、`settle_pending` 仍是进程内的；当前固定单 worker。
- 设备连接数/建立速率限制未实现。
- 音频链路（二进制帧语义、ASR/TTS、半双工、barge-in）、Phase 3 双模拟器、Android App、
  ESP32 固件未开始。
- `handle()` 内部异常路径会先 flush 再 close；若 handler 自身持续失败，仍可能丢该条上报。

## 7. 下一步

1. 提交本轮修复并推送。
2. 进入 Phase 3：`device_simulator`（`--protocol-version 1/2`）+ `app_simulator` + 真实
   uvicorn/WebSocket 端到端测试。
3. 音频链路留到后续音频 Block，模拟器不能代替真机验收。
