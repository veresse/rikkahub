# 13 · Phase 3 二次审查修复：撤销窗口、上报回写与验证手段

- 日期：2026-11-26
- 状态：✅ 完成（全量 190 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`f9e506e5..7b8d771f`（日志 12 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

上一轮修复（日志 12）把「先置 `closed` 再停 writer」改成「先冲刷再置 `closed`」，
解决了丢最后一条上报的问题，但审查指出这个顺序引入了两个**只在撤销路径可见**的回归：

1. 冲刷的最后一条上报会把 `devices.state` 从 `revoked` 覆盖回 `speaking`（违反撤销路由的显式契约）。
2. 冲刷期间会话仍是 `closed=False` / current link，App 的 `remote.*` 仍能下发到**已被撤销**的设备，
   并且收到 `ok=true` 的 ack。

另外一条与本轮主题直接相关的隐藏缺陷：`_safe_send()` 在传输层写失败时置 `closed=True`，
会让随后 `close()`/`_teardown()` 的冲刷被 `_handle_report()` 丢弃——同一类「用错标志位」的问题。

本轮修掉这三条，并处理上一轮审查的 Minor（限速窗口、日志级别副作用、CLI 退出码、
模拟器关闭超时、协议文档矛盾、`python -m` 的 RuntimeWarning）。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | 新增 `closing` 标志与 `_close_lock`；`send()` 拒绝 closing；`close()` 幂等；`_persist_report()` 不再回写已停用设备的运行态；`_safe_send()` 只置 `closing` |
| `companion/server/tests/test_device_gateway.py` | 新增 3 条回归：冲刷期拒指令、真实 `DELETE` 撤销后状态保持、写失败后仍冲刷上报 |
| `companion/simulators/device_simulator.py` | 握手失败非零退出码；`close()` 的 `ws.close()` 加上界；`_closing` 阻止关闭后派发新命令 |
| `companion/simulators/app_simulator.py` | 关闭时给等待者 `ConnectionError` 而不是裸取消 |
| `companion/simulators/__init__.py` | 改为惰性导出，消除 `python -m companion.simulators.device_simulator` 的 runpy RuntimeWarning |
| `companion/tests/test_phase3_e2e.py` | 限速用例固定 60s 窗口；日志用例用 monkeypatch 管级别；新增坏 Token CLI 用例 |
| `companion/docs/protocol_v2.md` | §4 前言标注 fire-and-forget 例外；§4.5 `idle.set` 示例补 `action_id` |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 13 |

## 3. 缺陷与修复

### 3.1 撤销后 `state` 被最后一条上报覆盖

`revoke_device()` 先提交 `state="revoked"`，再 `await link.close(4401)`；`close()` 现在会在
置 `closed` 之前冲刷上报，而 `_persist_report()` 无条件写
`device.state = report["state"]`、`device.last_seen_at`，于是撤销后设备在 `GET /api/devices`
里又显示成 `speaking`。

修复：`_persist_report()` 先判断生命周期字段。

```python
if not device.is_active:
    # 已撤销/停用的设备不再回写运行态：
    # state="revoked" 不能被最后一条上报覆盖成 speaking。
    device.last_report_json = report
    await db.commit()
    return
```

保留 `last_report_json` 既能让「冲刷确实发生」可观测，又不动 `state`/`last_seen_at`/音量等运行态。
`close()` 会等待冲刷完成，因此任何在撤销前开始的上报写入都发生在撤销提交之后，判断稳定；
撤销之后才到达的上报也会读到 `is_active=False`。

### 3.2 撤销冲刷窗口内仍能下发指令

`close()` 的第一个 `await`（拿锁/冲刷）之后才置 `closed`，而 `send()` 只看 `closed`：

```python
DURING REVOKE ACK: {'type': 'remote.ack', 'ok': True, 'action_id': '8f64729f-...'}
DEVICE GOT action.play DURING REVOKE: [{'type': 'action.play', 'name': 'nod', ...}]
```

（上述输出来自本轮审查者的独立复现，修复前。）

修复：`DeviceSession` 增加 `closing`，在 `close()` 的**第一个 `await` 之前**置位；
`send()` 对 `closing or closed` 返回 `device_offline`；`_handle_report()` 仍只看 `closed`，
所以冲刷照常落库。`close()` 用 `_close_lock` 串行化并短路重复调用，保证撤销（4401）与替换（4409）
并发时关闭码确定。

### 3.3 `_safe_send()` 用 `closed` 标记会毒化后续冲刷

写失败意味着「不要再往这条链路写」，但 `_teardown()`/`close()` 的冲刷依赖 `closed=False`：
原来 `_safe_send()` 直接置 `closed=True`，随后 `stop_writer()` 冲刷出的上报会被
`_handle_report()` 的第一行丢弃。改为只置 `closing=True`，并新增回归
`test_send_failure_keeps_flushing_later_reports`（写失败后推送一条上报，断言仍然落库）。

## 4. 新增回归与有效性验证

| 用例 | 断言要点 |
| :--- | :--- |
| `test_close_rejects_commands_while_flushing` | 用慢 handler 卡住冲刷；`close()` 未返回时 `send(idle.set)` 必须 `failed/device_offline`，且通道里没有该帧；重复 `close(4409)` 不修改关闭码 |
| `test_revoke_keeps_revoked_state_and_persists_last_report` | 走真实 `DELETE /api/devices/{id}`：`is_active=False`、`state == "revoked"`、`last_report_json["volume"] == 88`（证明冲刷过）、`volume` 保持 65（运行态未回写） |
| `test_send_failure_keeps_flushing_later_reports` | 写失败后 `closing=True/closed=False`；随后的上报仍写入 `device.volume` 与快照 |
| `test_device_simulator_cli_fails_on_bad_token` | 坏 Token 时 CLI 退出码非 0，stderr 含 `handshake failed` |

**有效性验证方式（偏差）**：原计划沿用前几轮的 `git stash` 红/绿对比，但旧代码缺少 `closing`
且用例当时没有等待上界，`pytest` 会无界挂起（两次尝试都在打印一个 `F` 后卡住，只能杀进程）。
改为一次性脚本在内存里把三处修复改回旧行为、跑用例、`finally` 还原文件：

```text
E           AssertionError: assert 'speaking' == 'revoked'
FAILED test_close_rejects_commands_while_flushing
FAILED test_revoke_keeps_revoked_state_and_persists_last_report
FAILED test_send_failure_keeps_flushing_later_reports
3 failed, 26 deselected in 0.72s
EXIT: 1
RESTORED
```

同时把用例本身加上了界（`wait_for(send, 2.0)`、`finally: release.set()`），
下次回归时只会失败，不会再挂住测试进程。

## 5. 其他 Minor 处理

| 问题 | 处理 |
| :--- | :--- |
| 限速用例仍依赖 1s 墙钟窗口 | 同时 `monkeypatch` `MAX_REMOTE_PER_SECOND=3` 与 `RATE_WINDOW_SECONDS=60.0` |
| 日志用例 `setLevel` 是进程级副作用 | 改用 `monkeypatch.setattr(logger, "level", ...)`，用例结束自动恢复；文档注明第三方库日志不在断言范围 |
| CLI 握手失败仍退出 0 | `main()` 在 `hello_ack is None` 时打印 `handshake failed` 并 `SystemExit(1)`，附坏 Token 用例 |
| 模拟器 `ws.close()` 可能等 10s | `asyncio.wait_for(ws.close(), 2.0)`；并加 `_closing` 让关闭后不再派发新命令 |
| App 模拟器 listener 被裸取消 | 改为 `set_exception(ConnectionError)`，等待方拿到明确错误 |
| 协议 §4 前言「所有消息都带 action_id」与 §4.4 矛盾 | 前言标注 §4.4 / §4.7 `calibrate_preview` 为例外 |
| §4.5 `idle.set` 示例缺 `action_id`（服务端实际带） | 补上并注明必须回执 |
| `python -m ...device_simulator` 的 runpy RuntimeWarning | `simulators/__init__.py` 改为 `__getattr__` 惰性导出 |

### 接受的风险（不修）

- **`_install()` / `close()` 在持有 `_device_lock` 时冲刷**：正常路径（writer 空闲）几乎无感，
  但极端情况下 `DELETE` 响应与替换流程会被 DB（SQLite `busy_timeout` 15s）或卡住的 App 广播拖慢。
  拆分锁需要重新设计「替换期间旧链路是否仍是 current」的时序，收益不抵复杂度；记录为已知延迟上界。
- **`expression.set` 不校验 `actions.json.expressions`**：模拟器对任意字符串回 `done`；
  服务端目前未开放该指令，等固件 Block 一起对齐。
- **`results_sent` 只在发送成功后记录**：当前无消费者，行为变化不影响断言。

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
29 passed in 1.02s
29 passed in 1.03s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py -k "cli or rate_limit or logs"
4 passed, 12 deselected in 8.25s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 11.75s
16 passed in 11.72s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
190 passed in 16.51s
190 passed in 16.24s

$ & "...\companion-venv\Scripts\python.exe" C:\Users\ctr\AppData\Local\Temp\opencode\mutation_check.py
3 failed, 26 deselected in 0.72s
RESTORED
```

## 7. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 用 `closing` 而不是提前置 `closed` | 提前 `closed` 会让冲刷自己的上报被丢弃（日志 12 修的就是这个） | 所有「链路是否还能写」的判断点都必须记得看 `closing`；目前只有 `send()` 与 `_safe_send()` |
| 已停用设备只保留快照、不回写运行态 | `state="revoked"` 是撤销路由的显式契约，运行态对已撤销设备无意义 | 若将来需要在 UI 上展示「撤销前最后状态」，要读 `last_report_json` 而不是列字段 |
| 撤销路由不再二次写 `revoked` | `close()` 会等冲刷结束，`is_active` 判断足以覆盖；再加一次 UPDATE 只会掩盖前面的顺序问题 | 若未来出现绕过 `close()` 的上报写入路径，需要重新加路由级兜底 |
| 回归验证改用内存 mutation 脚本 | 旧代码下用例会无界挂起，`git stash` 方式不可控（已两次卡住） | 脚本只还原三处守卫，不能证明「整个旧文件」的红；量化证据以审查者独立复现补足 |
| 接受 `_device_lock` 内冲刷的延迟 | 拆分锁需要重排替换/撤销时序，风险高于收益 | 卡住的 App 连接会把 `DELETE` 拖到秒级；届时再引入广播超时或异步 publish |

## 8. 未完成项 / 阻塞点

- 模拟器仍未实现设备侧 `>10 次/秒` 拒绝、`max_duration_ms` 看门狗、越界角度截断（固件 Block 一起做）。
- e2e 仍未覆盖 `servo.move`、`idle.set`、校准 preview/save、4409 替换、4408 握手超时/非法首帧（行为已由
  FakeChannel 用例或审查者探针验证，属回归覆盖缺口）。
- 上行 `device.report`/`action.result` 是否带 `session_id`/`turn_id`：`protocol_v2.md` §2 与 §5 示例不一致，待硬件 Block 定稿。
- 路由错误体 `detail` 仍为中文可读文案，与 §11.6 的「detail 为 code」不一致，未统一。
- 单 worker 限制、连接数上限、建连速率限制未实现。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `7b8d771f..HEAD`。
3. 审查通过后进入音频 Block；音频与舵机行为必须真机验收，模拟器不算数。

## 勘误（第八轮审查后补充，详见日志 14）

1. **§3.1 的因果不成立。** 原文说「`close()` 会等待冲刷完成，因此任何在撤销前开始的上报
   写入都发生在撤销提交之后，判断稳定」，方向恰好相反：writer 可能已经 `db.get()` 读到
   `is_active=True`，撤销随后提交，而这次上报的 `commit()` 才落地——`is_active` 判断仍然
   会看到旧值并把 `state` 写回 `speaking`。真正的修法是条件 UPDATE（日志 14 §3.1）。
2. **§7 表格「`is_active` 判断足以覆盖」同样不成立**，原因同上。
3. **§4 结尾「下次回归时只会失败，不会再挂住测试进程」不成立。** `closing` 检查改回旧行为时，
   用例里的 `while ...: await asyncio.sleep(0.01)` 没有上界，实测会挂死（审查者复现：
   `HUNG: pytest did not finish in 8s`）。日志 14 已改成有上界的 `wait_until()`。
4. **§3.2 的复现输出在修复后仍会出现 `ack ok=true`**，只是不再有 `action.play` 帧；要让 App
   在撤销窗口直接拿到 `device_offline`，还需要 App 网关检查 `closing`（日志 14 §3.3）。
