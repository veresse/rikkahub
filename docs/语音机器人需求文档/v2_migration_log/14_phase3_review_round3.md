# 14 · Phase 3 三次审查修复：撤销生命周期竞态与「在线但不可控」

- 日期：2026-11-26
- 状态：✅ 完成（全量 193 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`9f662552`（日志 13 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

第八轮独立审查对 `9f662552`（日志 13）给出 2 个 Critical、2 个 Important、4 个 Minor。
本轮逐条处理，重点是两条仍可复现的**撤销生命周期缺陷**：

1. **C1**：`_persist_report()` 仍是「读 `is_active` → 改 ORM → commit」。writer 已经读到
   `is_active=True`、撤销随后提交时，这次上报的 commit 会把 `state="revoked"` 覆盖回
   `speaking`。日志 13 里「`close()` 会等冲刷结束，判断稳定」的说法方向搞反了（已在日志 13
   追加勘误）。
2. **C2**：设备在撤销前通过 `/device/ws` 鉴权、但在撤销提交后才发 `hello`。撤销路由当时查
   `get_device_link()` 得到 `None`，于是不关闭；随后 `handle()` 照常注册链路，已撤销设备
   重新上线并可被 App 控制。

另外修掉：`test_close...` 在核心 `closing` 回退时会**挂死**（I1）；写失败后 `closing=True`
但 `get_status()["online"]` 仍为 `True`、App 仍能拿到 `ok=true` 的 ack（I2 / M1）；
已撤销设备的最后一条上报表仍会被广播成 `state=speaking`（M2）；协议 §4 前言仍漏
`action.stop`（M3）；以及日志注释 / 模拟器异常路径 / e2e 时间余量（M4）。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `_persist_report()` 改条件 UPDATE 并返回是否活跃；`handle()`/`_install()` 复核撤销；`_install()` 先注册后回 ack、返回 bool；`get_status().online` 纳入 `closing`；`_handle_report()` 对已撤销设备不广播；`_persist_hello()` 跳过已撤销设备；撤销拒绝时 offline 事件原因改为 `device_revoked` |
| `companion/server/app/services/device_registry.py` | 新增每设备生命周期锁 `device_lifecycle_lock()`（锁放在注册表，跨网关实例共享） |
| `companion/server/app/routers/devices.py` | `DELETE /{device_id}` 的「查链路 + close(4401)」放进同一把生命周期锁 |
| `companion/server/app/services/app_gateway.py` | `closing` 链路直接回 `ack ok=false device_offline`，不再先 `ok=true` 再补失败终态 |
| `companion/server/tests/test_device_gateway.py` | 新增 2 条回归（撤销与在途落库交错、撤销与迟到 hello）；`closing` 用例改为有上界等待；写失败用例断言 `online=False` |
| `companion/server/tests/test_app_gateway.py` | 新增 `closing` 链路拒绝遥控的用例 |
| `companion/simulators/device_simulator.py` | `websockets.connect()` 被 HTTP 层拒绝时折算为 `hello_ack=None`，CLI 仍以非零码暴露失败 |
| `companion/tests/test_phase3_e2e.py` | 日志用例注释与实现对齐；CLI 常驻用例等待 6s → 7s（旧实现 5s，留 2s 余量） |
| `companion/docs/protocol_v2.md` | §4 前言补充 §4.6 `action.stop` 例外 |
| `docs/语音机器人需求文档/v2_migration_log/13_phase3_review_round2.md` | 追加「勘误」章节，纠正 4 处不成立的结论 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 14 |

## 3. 缺陷与修复

### 3.1 C1：在途上报仍可覆盖 `state="revoked"`

复现（审查者探针，修复前）：

```text
PROBE race: persist read is_active=True and is paused
PROBE race: DELETE committed, state= revoked
PROBE race FINAL: is_active= False state= speaking volume= 88
```

读-改-写天然有窗口，靠 `close()` 的冲刷顺序无法消除：冲刷本来就发生在撤销提交之后。
改成一条带条件的 UPDATE，由数据库原子判定：

```python
result = await db.execute(
    update(DeviceModel)
    .where(DeviceModel.id == device_id, DeviceModel.is_active.is_(True))
    .values(volume=..., idle_enabled=..., state=..., ..., last_report_json=report,
            last_seen_at=now_ms())
)
active = bool(result.rowcount)
if not active:
    # 已撤销/停用：只保留快照，运行态（含 state="revoked"）不动。
    await db.execute(
        update(DeviceModel).where(DeviceModel.id == device_id)
        .values(last_report_json=report)
    )
```

`_persist_report()` 现在返回 `active`，供 `_handle_report()` 决定是否广播（M2）。

### 3.2 C2：撤销后仍能注册「已鉴权、迟到 hello」的连接

原流程：`ws_auth`（此时 `is_active=True`）→ 等 `hello`（最长 5s）→ 撤销提交并查链路
（`None`）→ `hello` 到达 → `_install()` 注册成功 → 设备在线且可控。

修复分两层：

- `handle()` 收到 `hello` 后、回 ack 之前复核 `is_active`，已撤销直接 `close(4401)`；
- `_install()` 在**每设备生命周期锁**内再复核（权威判定），失败则 `close(4401)` 并返回 `False`；
- `DELETE /api/devices/{id}` 的「查链路 + close(4401)」使用同一把锁。

锁移到 `device_registry`，让路由与任意 `DeviceGateway` 实例共享。四种交错都成立：
撤销先提交则 install 读到 `False`；install 先注册则撤销在锁内关掉它；两者不会同时看到
「链路不存在 + 设备活跃」。

顺带调整顺序：`_install()` → `_send_hello_ack()`。原顺序下 ack 可能早于链路注册
（多一次 DB 读就让测试观察到「收到 ack 但 `get_device_link()` 还是 None」），App 拿到上线
事件后立刻发指令会吃到 `device_offline`。现在 ack 意味着「链路已可用」。

### 3.3 I2 / M1：`closing` 期间对外仍是「在线」

`closing` 只拦了 `DeviceSession.send()`，于是：

- `remote.action.play` 先回 `ack ok=true`（已入队），再补 `action.result failed/device_offline`；
- `get_status()["online"]` 仍为 `True`，LLM 的 `robot.get_status` 也会说在线；
- 写失败（`_safe_send`）后若读循环没退出，会话会长期「在线但不可控」。

修复：

- `get_status()`：`"online": not (self.closed or self.closing)`；
- App 网关在解析到 `closing` 链路时直接 `ack ok=false device_offline`。

不引入「写失败后自动 close」：那会让 `close()` 立刻停止 writer，随后到达的上报被
`queue_report` 丢弃，正好破坏日志 12 修好的「写失败后仍要冲刷已入队上报」。当前语义是
「不可控但继续收尾」，对 App 表现为离线。

### 3.4 M2：已撤销设备的最后一条上报仍被广播

`_handle_report()` 现在按 `_persist_report()` 的返回值跳过广播。DB 早已不回写运行态，
但事件流会先发 `device.report state=speaking` 再发 `device.status online=false`，App 若按
事件驱动卡片会短暂显示已撤销设备在工作。

### 3.5 I1：回归时挂死而不是失败

`test_close_rejects_commands_while_flushing` 原来用无上界的

```python
while not close_task.done() and not getattr(session, "closing", False):
    await asyncio.sleep(0.01)
```

核心修复回退时 `closing` 永远不出现、`close_task` 卡在慢 handler 里，用例挂死（审查者实测
`HUNG: pytest did not finish in 8s`）。改成 `wait_until(...)`（2s 上界），`finally` 里
`release.set()` + `wait_for(close_task, 5.0)`；回归时是 `TimeoutError` 失败，2.6s 内结束。

### 3.6 M3 / M4

| 问题 | 处理 |
| :--- | :--- |
| 协议 §4 前言漏 `action.stop` | 例外列表补 §4.6（最高优先级、无 `action_id`、不等回执） |
| e2e 日志用例注释与「按 logger 名过滤」实现矛盾 | 注释改为「断言仍按 logger 名过滤掉第三方库记录」 |
| 模拟器 HTTP 层被拒时 stderr 不含 `handshake failed` | `websockets.connect()` 包一层，折算为 `close_reason=connect_failed: ...` + `hello_ack=None` |
| CLI 常驻用例只比旧超时多 1s | 6s → 7s |
| 撤销拒绝时 offline 事件 `reason=handshake_failed` | 新增 `handshake_reason`，撤销场景发 `reason=device_revoked`，并在用例中断言 |

## 4. 新增回归与有效性验证

| 用例 | 断言要点 |
| :--- | :--- |
| `test_revoke_while_report_write_in_flight_keeps_revoked_state` | 用 `PausingSessionFactory` 把上报卡在「已读到设备 / 写库之前」；后台发起真实 `DELETE`，等撤销事务提交后放行。断言 `is_active=False`、`state=revoked`、`volume=65`、`last_report_json.volume=77` |
| `test_revoke_during_hello_wait_blocks_late_registration` | 已鉴权但未 hello 时走真实 `DELETE`，随后发 hello：必须 `close(4401)`、不注册链路、不回 `hello_ack`、offline 事件 `reason=device_revoked` |
| `test_closing_link_is_offline_without_dispatching_commands` | App 网关对 `closing` 链路：`remote.action.play` / `remote.get_status` 都 `ok=false device_offline`，链路收不到任何指令 |
| `test_close_rejects_commands_while_flushing`（改） | 有上界等待；`closing` 期间 `get_status()["online"] is False` |
| `test_send_failure_keeps_flushing_later_reports`（改） | 写失败后 `closing=True`、`closed=False` 且 `online=False`，随后上报仍落库 |

**红/绿证据**（临时脚本在内存里改回旧行为，`finally` 还原，工作区最终干净）：

```text
[C1: drop is_active predicate from report UPDATE] exit=1
E             - revoked
E             + speaking
1 failed
[C1 ...] restored

[C2: remove both revocation rechecks] exit=1
E       assert <DeviceSession ...> is None
1 failed
[C2 ...] restored

[I1: drop early closing flag in close()] exit=1
E                   TimeoutError
1 failed in 2.62s          # 有界失败，不再挂死
[I1 ...] restored

[I2: closing no longer reported offline] exit=1
E       assert True is False
1 failed
[I2 ...] restored

[M1: app gateway accepts closing link] exit=1
E           assert True is False
1 failed
[M1 ...] restored
```

**过程偏差（自我记录）**：第一版 mutation 脚本用第一次出现的
`self.closing = True` 做替换，实际命中的是 `_safe_send()` 里的赋值而不是 `close()` 里的，
于是 I1 显示「仍然绿」。换成以「紧随其后的注释」为锚点后得到上述 `TimeoutError` 红。
这也是「用字符串替换做 mutation 必须确认命中位置」的一个实例。

`PausingSessionFactory` 会同时挂在 `Session.get()` 返回后与 `execute()` 之前：
旧实现（读 ORM）在新钩子下停在读之后，新实现（条件 UPDATE）停在写之前，两条路径都能
被同一用例覆盖。

## 5. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
193 passed in 17.34s
193 passed in 17.12s
193 passed in 17.54s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 12.92s
16 passed in 12.78s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
50 passed in 1.22s
50 passed in 1.24s
50 passed in 1.25s
50 passed in 1.35s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
31 passed in 1.10s

$ & "...\companion-venv\Scripts\python.exe" C:\Users\ctr\AppData\Local\Temp\opencode\round8_mutation_check.py
... 5 mutations, 4 red on first run; I1 anchor fixed and re-run red (TimeoutError)
```

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 运行态落库改条件 UPDATE，不再读 ORM | 读-改-写窗口无法靠调度顺序消除；撤销与上报天然并发 | 读不到 `last_seen_at`/旧值做比较；快照与运行态由两条语句写，`is_active` 翻转时以第二条为准 |
| 生命周期锁放注册表而非网关实例 | 撤销路由不持有网关实例；同进程多网关必须互斥 | 锁随设备数增长（无回收）；`reset_device_links()` 会清空锁，只能在无任务时调用 |
| `_install()` 先注册再回 ack | 否则 App 可能在 ack 与注册之间发指令，拿到 `device_offline` | 设备收到 ack 前链路已可见，理论上会收到早到的指令；固件需容忍 |
| 写失败不自动 close | 自动 close 会立刻停 writer，丢掉随后到达的上报（日志 12 的修复目标） | 链路可能长期停留 `closing` 直到读循环退出；期间只能靠重连替换 |
| App 网关对 `closing` 直接回 `device_offline` | 避免 `ok=true` 后紧跟失败终态，语义更接近「不可控即离线」 | 急停 `remote.action.stop` 也会被拒；此时待执行动作已由 `close()` 全部置为失败，无实际损失 |
| 已撤销设备不广播运行态 | App 事件流不应把 revoked 设备显示成 speaking | 若将来需要「撤销前最后状态」的实时事件，要另发显式事件 |
| `_device_is_active()` 在 DB 异常时按活跃处理 | 该检查是撤销窗口的补充防线；DB 抖动时 `_load_device()` 已先失败 | DB 故障叠加撤销时窗口可能重现，但这属于更大故障 |
| e2e CLI 等待改 7s | 旧实现 5s 超时，1s 余量在慢 CI 上可能假绿 | e2e 多花 1s |
| 修正日志 13 用「勘误」而非改写原文 | 保留当时的判断与后续纠正，便于追溯 | 日志需读到最后才看到更正 |

### 接受的风险（不修）

- **`_install()` / `_teardown()` / 撤销路由在持锁时等待 flush 或 DB**：`DELETE` 与替换在
  极端情况下可被 `busy_timeout`（15s）或卡住的广播拖到秒级。拆分锁需要重排「替换期间旧链路
  是否仍是 current」的时序，收益不抵复杂度；记录为已知延迟上界。
- **`closing` 期间仍有上报写入**：这是有意的（收尾），`get_status()` 已按离线呈现。
- **模拟器未实现设备侧 >10 次/秒拒绝、`max_duration_ms` 看门狗、越界角度截断**。
- **e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408**（FakeChannel 已覆盖）。
- **单 worker 限制、连接数上限、建连速率限制未实现**。

## 7. 未完成项 / 阻塞点

- 上行 `device.report`/`action.result` 是否带 `session_id`/`turn_id`：`protocol_v2.md` §2 与 §5
  示例仍不一致，待硬件 Block 定稿。
- 路由错误体 `detail` 仍为中文可读文案，与 §11.6 的「detail 为 code」不一致。
- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。

## 8. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `9f662552..HEAD`。
3. 审查通过后进入音频 Block。
