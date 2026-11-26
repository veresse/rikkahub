# 15 · Phase 3 四次审查修复：握手就绪门控与统一在线判定

- 日期：2026-11-26
- 状态：✅ 完成（全量 198 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`9f662552..3c972da7`（`3c972da7` 即日志 14 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

第九轮独立审查对 `3c972da7` 给出 **0 Critical / 3 Important / 7 Minor**，结论为
“With fixes”。核心问题是上一轮把「注册链路」与「可以下发」当成了同一件事：

1. **I1（握手顺序回归）**：`_install()` 先 `register_device_link()` 并广播
   `device.status online=true`，之后才 `_send_hello_ack()`。两者之间有 await，App/LLM
   可以在这段窗口下发控制帧；而协议 §2 的 `session_id` 在 `hello_ack` 里。自带
   `device_simulator` 明确把 ack 之前的首个非 `hello_ack` 帧当作握手失败。
2. **I2（`closing` 未闭环）**：`get_status()` 与 App 网关已把 `closing` 当离线，但
   `orchestrator.py` / `action_tools.py` 仍用 `link is not None` 判断在线，LLM 会拿到
   `robot.*` 工具却每次都失败；`_handle_report()` 在 `closing` 期间仍广播运行态。
3. **I3（权威判定缺测）**：`test_revoke_during_hello_wait_blocks_late_registration`
   的时序是撤销先完成、hello 后到，`handle()` 预检就能拦住，`_install()` 的锁内复核
   从未执行。只删锁内复核时该用例仍绿。
4. Minor：`_persist_hello()` 仍是读-改-写；`closing` 期间 App 急停被提前拒绝、
   调度器 `stop()` 不执行；`reset_device_links()` 清空生命周期锁；失败握手的
   offline 事件语义不一致；M2 广播措辞过宽；协议 §4.6 回执表述有歧义；日志 13 末尾空行。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `DeviceSession.ready` 门控；`send()`/`get_status()` 在 ack 前按离线；`_install()` 只注册、`_mark_ready()` 在 ack 后置 ready 并广播上线；`_persist_hello()` 改条件 UPDATE；`_handle_report()` 在 `closing` 时只落库不广播；`_install()` 拒绝时补发 `device_revoked` 事件 |
| `companion/server/app/services/device_registry.py` | 新增 `device_link_is_online()`；生命周期锁改为「按事件循环分表 + 弱引用回收」，`reset_device_links()` 不再清锁 |
| `companion/server/app/services/orchestrator.py` | 机器人工具注入改用 `device_link_is_online(link)`，不再只看 `link is not None` |
| `companion/server/app/services/app_gateway.py` | `closing` 分支对 `remote.action.stop` 仍调用 `scheduler.stop()`，确保本地在途/排队动作结算 |
| `companion/server/tests/test_device_gateway.py` | 新增 ack 前不可控、预检后撤销（锁内复核）、hello 落库与撤销交错、在线判定四个用例；`PausingSessionFactory` 支持 `pause_on_get`；写失败用例改为断言「落库但不广播」 |
| `companion/server/tests/test_app_gateway.py` | 新增 `closing` 链路急停仍结算本地动作的用例 |
| `companion/docs/protocol_v2.md` | §4.6 明确 `action.stop` 不带 `action_id`、不等待回执；设备按原动作回执合法但迟到会被丢弃 |
| `docs/语音机器人需求文档/v2_migration_log/13_phase3_review_round2.md` | 删除文件末尾多余空行（`git diff --check`） |
| `docs/语音机器人需求文档/v2_migration_log/14_phase3_review_round3.md` | 追加「勘误」，纠正 ack 可用、`closing` 闭环、hello 落库、广播措辞、清锁等表述 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 15 |

## 3. 缺陷与修复

### 3.1 I1：`hello_ack` 之前可下发指令

原顺序：

```text
_install(): register link + publish online=true
_send_hello_ack()      <- await，窗口在这里
_mark... （不存在）
_read_loop()
```

审查者用阻塞 ack 的 FakeChannel 复现：

```text
SENT ORDER ['servo.calibrate_preview', 'hello_ack']
SUBSCRIBER ['subscriber send -> done']
```

模拟器 `companion/simulators/device_simulator.py` 的 `connect()` 只接受首个非
`hello_ack` 帧为握手失败，因此这是实际回归，不是理论风险。

修复：注册仍然早于 ack（否则撤销路由查不到链路，日志 14 的 C2 会重现），但引入
`DeviceSession.ready`：

- `ready = False` 初始值；
- `send()`：`if not self.ready or self.closing or self.closed` → `failed/device_offline`；
- `get_status()["online"] = self.ready and not (closed or closing)`；
- `_install()` 只负责注册/启动 writer/替换旧链路，不再广播上线；
- ack 成功后才 `_mark_ready(session)`：在生命周期锁内确认它仍是当前链路，置
  `ready=True`，再 `_publish_status(online=True)`。

这样 App 收到 `online=true` 时，设备一定已经拿到 `session_id`，且注册表里的链路可下发。

### 3.2 I2：统一在线判定，`closing` 期间不广播运行态

新增 `device_registry.device_link_is_online(link)`：

```python
return (
    link is not None
    and getattr(link, "ready", True)
    and not getattr(link, "closing", False)
    and not getattr(link, "closed", False)
)
```

- `orchestrator.py` 用 `online=device_link_is_online(link)` 决定是否注入 `robot.*`；
- `is_device_online(device_id)` 改为读取同一判定；
- `_handle_report()` 在首次落库前**不**因 `closing` 跳过（`close()` 靠这条路径冲刷），
  但落库后若 `closing` 则不再 `bus.publish` `device.report`。

`getattr(..., "ready", True)` 让只实现 `DeviceLink` 协议的测试替身/未来链路默认可用，
不会因为缺少属性被误判离线。

### 3.3 I3：补上 `_install()` 锁内复核的回归

`PausingSessionFactory` 增加 `pause_on_get=N`，在第 N 次 `get()` 返回后暂停。新用例
`test_revoke_after_precheck_is_caught_by_install_lock`：

1. `pause_on_get=2`：`_load_device()` 是第 1 次，`handle()` 预检是第 2 次；
2. 预检的 `get()` 已拿到 `is_active=True` 的旧对象，但还没判断；
3. 暂停期间用真实 `DELETE /api/devices/{id}` 提交撤销；
4. 放行后预检看到旧值而通过，`_persist_hello()` 的条件 UPDATE 不命中，
   `_install()` 在生命周期锁内重新查库得到 `is_active=False` → `close(4401)`、不注册、
   不回 ack、广播 `device_revoked`。

删除 `_install()` 的复核时该用例红（`get_device_link()` 变为非 None）；只删预检时
用例不会以断言失败收场，而是 `pause_on_get=2` 落进 `_install()` 持锁的 `get()`，
与测试线程的 `DELETE` 争同一把生命周期锁，5.87s 内 `TimeoutError`（已加 `wait_for`
上界）。这说明用例对预检位置有强依赖，覆盖的确实是锁内复核（详见日志 16）。

### 3.4 Minor：`_persist_hello()` 改条件 UPDATE

```python
values = {"protocol_version": ..., "capabilities_json": ..., "last_seen_at": now_ms()}
if isinstance(firmware, str):
    values["firmware_version"] = firmware[:32]
await db.execute(
    update(DeviceModel)
    .where(DeviceModel.id == device_id, DeviceModel.is_active.is_(True))
    .values(**values)
)
```

新用例 `test_persist_hello_does_not_refresh_revoked_device` 在 `execute()` 前暂停，
提交撤销后放行：旧实现会写入 `firmware_version="9.9.9"` 并刷新 `last_seen_at`；
新实现三个字段都保持原值。

### 3.5 Minor：`closing` 期间急停仍结算本地动作

App 网关的 `closing` 分支原本直接回 `device_offline` 并 return，绕过了
`action_orchestrator.stop()`。现在：

```python
if getattr(link, "closing", False):
    if message_type == "remote.action.stop":
        scheduler = get_device_scheduler(device_id, link)
        await scheduler.stop(message.get("reason", "user_stop"))
    await session.send_json(_ack(request_id, False, "device_offline"))
    return
```

急停帧本身仍发不出去（链路已不可写，这点没有实际损失），但本地 current/queue 会按
`failed/stopped` 结算，App 不必等 `action.result` 超时。

### 3.6 Minor：生命周期锁不再被 `reset_device_links()` 清空

锁是 `asyncio.Lock`，被竞争后会绑定首次使用的循环。旧实现把锁放在单一
`dict[device_id, Lock]` 并在测试清链路时一起清空；如果未来在任务仍持有旧锁时调用，
会为同一设备生成第二把锁，破坏撤销/安装互斥。

改为：

```python
_LIFECYCLE_LOCKS = weakref.WeakKeyDictionary()  # loop -> {device_id: Lock}

def device_lifecycle_lock(device_id):
    loop = asyncio.get_running_loop()
    locks = _LIFECYCLE_LOCKS.setdefault(loop, {})
    ...
```

- 同一条循环内仍然严格互斥；
- 每个 pytest 用例的新循环有独立表，不会跨循环复用已绑定的锁；
- 循环结束后整表被弱引用回收，不再需要清锁。

### 3.7 Minor：失败握手的 offline 事件

`_install()` 拒绝撤销连接时补发 `device.status online=false reason=device_revoked`，与
`handle()` 预检路径保持一致。`_teardown()` 在 `was_current=False` 时不再发事件，
因此不会重复。

预检路径对「从未上线」的连接也会发一次 `device_revoked` offline；这属于幂等的状态
纠正（设备确实被撤销且当前离线），保留。

### 3.8 M2 边界与协议 §4.6

- `_handle_report()` 的「已撤销不广播」只保证「落库时已撤销」；如果条件 UPDATE 先
  提交（当时仍 active）、撤销随后提交，事件仍可能先发 `device.report` 再发 offline。
  强制闭包需要广播前二次查库，收益不抵一次额外 DB 往返，保留为已知边界（日志 14
  勘误已注明）。
- 协议 §4.6 改为：`action.stop` 自身不带 `action_id`，服务端不等待回执；设备按被打断
  动作的原 `action_id` 回 `failed/stopped` 合法，但服务端已结算，迟到回执会被丢弃。

## 4. 新增/修改回归与有效性验证

| 用例 | 断言要点 |
| :--- | :--- |
| `test_link_is_not_controllable_before_hello_ack` | ack 阻塞期间链路已注册但 `ready=False`、`online=False`、`send()` 返回 `device_offline` 且无控制帧落到 channel、无 `device.status`；放行 ack 后才 `online=True` |
| `test_revoke_after_precheck_is_caught_by_install_lock` | 预检读到旧 active 快照后撤销；`_install()` 锁内复核必须拒绝：不注册、`close(4401)`、无 ack、offline 事件 `device_revoked` |
| `test_persist_hello_does_not_refresh_revoked_device` | hello 落库前撤销：`firmware_version`/`capabilities_json`/`last_seen_at` 均不更新，`state` 保持 `revoked` |
| `test_device_link_is_online_requires_ready_and_not_closing` | 在线判定对 `None`/未 ready/`closing`/`closed` 都返回 False |
| `test_closing_link_still_settles_local_stop` | `closing` 链路的 `remote.action.stop` 回 `device_offline`，但 `scheduler.stop("user_stop")` 被执行、无控制帧下发 |
| `test_send_failure_keeps_flushing_later_reports`（改） | 写失败后上报仍落库；`closing` 期间不再广播 `device.report` |

**红/绿证据**（临时脚本在内存中改回旧行为，`finally` 还原，工作区最终干净）：

```text
[I3: drop _install lock recheck] exit=1
E       AssertionError: 锁内复核必须阻止撤销后的注册
[I3 ...] restored

[I1: send ignores ready] exit=1
E       At index 0 diff: 'done' != 'failed'
[I1 ...] restored

[I1: get_status ignores ready] exit=1
E       assert True is False
[I1 ...] restored

[M4: drop is_active predicate from hello UPDATE] exit=1
E       + 9.9.9
[M4 ...] restored

[I2: broadcast reports while closing] exit=1
E       AssertionError: assert not [{'type': 'device.report', ...}]
[I2 ...] restored

[M5: closing app gateway drops local stop] exit=1
E       Right contains one more item: 'user_stop'
[M5 ...] restored

MUTATIONS THAT STAYED GREEN: none
```

`reset_device_links()` 的去锁化没有做字符串 mutation：它不是「删一行守卫」就能红/绿的
行为，直接证据是同一套用例在「按循环分表」后连续多轮跨测试稳定通过。

## 5. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
198 passed in 17.27s
197 passed in 17.26s
197 passed in 17.25s
197 passed in 17.22s      # 加最后一个判定用例前为 197

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 12.77s
16 passed in 12.89s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
55 passed in 1.26s      # 35 + 20（提交 f2affdbc 时的实际数量）

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_app_gateway.py
20 passed in 0.39s

$ & "...\companion-venv\Scripts\python.exe" C:\Users\ctr\AppData\Local\Temp\opencode\round9_mutation_check.py
6 个 mutation 全部 exit=1；MUTATIONS THAT STAYED GREEN: none
```

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 注册仍早于 ack，但新增 `ready` 门控 | 注册必须早于 ack 才能让撤销路由查到链路（日志 14 C2）；又不能让 ack 前下发 | 多一个状态位；所有「能否下发/是否在线」的判断必须走 `ready` |
| 上线广播移到 ack 之后 | `online=true` 是对 App/LLM 的「可控制」承诺，必须晚于 `session_id` 到达设备 | ack 发送失败时设备不会上线（正确，连接会走错误路径） |
| 在线判定统一到 `device_link_is_online()` | 三处各自判断会继续漂移；`closing`/`ready` 是同一语义 | 协议测试替身缺少新属性时依赖 `getattr(..., True)` 默认可用 |
| `closing` 期间上报只落库不广播 | 事件流不应与 `get_status`/`remote` 的离线矛盾 | 写失败后仍想看实时上报的订阅者会收不到；重连后以快照为准 |
| 急停例外：`closing` 仍执行 `scheduler.stop()` | 不下发不等于不结算；否则本地任务要等超时 | 急停 ack 仍是 `device_offline`；App 需把「本地已停」与「设备未收到」分开理解 |
| 生命周期锁按循环分表 + 弱引用 | 既保留同循环互斥，又避免跨循环复用已绑定锁与「清锁产生第二把锁」 | 多循环并发访问同一设备时各自独立（本进程没有这种用法）；每循环表随设备数增长 |
| `_persist_hello()` 也改条件 UPDATE | 与 C1 同源：读-改-写窗口无法靠顺序消除 | `last_seen_at` 在撤销瞬间不再刷新（预期行为） |
| 不为 M2 广播前二次查库 | 额外 DB 往返；该窗口只在「落库后、广播前恰好撤销」的毫秒级交错出现 | App 可能短暂看到 revoked 设备的一次运行态事件；已在日志 14 勘误注明 |

### 接受的风险（不修）

- **失败握手的 offline 事件可能对「从未上线」设备发一次**：幂等的状态纠正，保留。
- **多进程/多 worker 下生命周期锁不跨进程**：当前 `main.py` 单进程；部署限制需在文档中
  明确（若将来多 worker，撤销 HTTP 可能落到不持有该 WS 的进程）。
- **`closing` 期间新上报被 writer 丢弃**：`_writer_stopping` 语义不变，收尾只冲刷已入队
  上报；与日志 12 的承诺一致。
- **模拟器未实现设备侧 >10 次/秒拒绝、`max_duration_ms` 看门狗、越界角度截断**。
- **e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408**（FakeChannel 已覆盖）。
- **上行是否带 `session_id`/`turn_id` 的文档歧义**、**`detail` 文案风格**、**单 worker 限制**。

## 7. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始；`device.report` 事件是否会让已 offline 的设备在 App 侧重新显示
  为在线，需要 App 实现时确认（不在本轮范围）。
- 真实 ESP32 固件是否容忍 ack 前的控制帧：本轮已通过门控消除该窗口，仍需硬件验收。

## 8. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `3c972da7..HEAD`。
3. 审查通过后进入音频 Block。
