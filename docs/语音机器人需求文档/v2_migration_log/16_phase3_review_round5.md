# 16 · Phase 3 五次审查修复：ack 后上线竞态与就绪判定收尾

- 日期：2026-11-26
- 状态：✅ 完成（全量 201 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`3c972da7..f2affdbc`（`f2affdbc` 即日志 15 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

第十轮独立审查对 `f2affdbc` 给出 **0 Critical / 1 Important / 6 Minor**，结论为
“With fixes”。本轮逐条处理：

1. **I-1（Important）**：`_mark_ready()` 只检查「链路仍是 current」，而撤销路由
   **不会把链路从注册表删除**，只 `close(4401)`。当 `hello_ack` 被慢通道阻塞、
   撤销在此期间提交时，ack 写成功后 `_mark_ready()` 仍会把 `ready=True` 并广播
   `online=true`，出现「撤销已提交 → 上线 → 下线」的事件序列，违背本提交自己
   声明的「ack 且链路有效后才上线」不变量。
2. Minor：
   - **M-1**：App 网关只挡 `closing`，不挡 `ready`；ack 前遥控会先回 `ok=true`，
     再补 `failed/device_offline`。
   - **M-2**：`orchestrator` 改用 `device_link_is_online()` 没有红灯测试。
   - **M-3**：日志 15 的 `52 passed` 应为 `55 passed`；「只删预检则仍绿」与实际
     失败模式（争锁超时）不符。
   - **M-4**：`pause_on_get=2` 对调用次数敏感，预检被删时用例会挂起而不是失败。
   - **M-5**：`is_device_online()` 变成死代码，且与 `device_link_is_online()` 语义重复。
   - **M-6**：被竞争过的 `asyncio.Lock` 会强引用其循环，`WeakKeyDictionary` 的
     value→key 环使「循环结束后整表回收」不成立。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | ack 返回后**立即**置 `session.ready=True`（避免 ack 与 ready 之间的 `device_offline` 窗口）；`_mark_ready()` 只负责上线广播，锁内复核 `closed/closing`、`get_device_link` 与 `is_active`，撤销时关闭并交给 `_teardown()` 发 offline |
| `companion/server/app/services/app_gateway.py` | 遥控入口改用 `device_link_is_online(link)`：`closing` 与未 `ready` 都直接回 `device_offline`；急停只在 `closing` 时结算本地调度器 |
| `companion/server/app/services/device_registry.py` | 删除死代码 `is_device_online()`；`device_lifecycle_lock()` 顺手剔除已关闭循环，处理 value→key 环 |
| `companion/server/tests/test_device_gateway.py` | 新增 `test_revoke_while_ack_blocked_does_not_broadcast_online`；预检用例的 `DELETE` 加 `wait_for(5s)`，让争锁死锁以失败结束 |
| `companion/server/tests/test_app_gateway.py` | 新增 `test_not_ready_link_is_offline_without_dispatching_commands` |
| `companion/server/tests/test_chat_action_loop.py` | 新增 `test_registered_but_not_ready_link_gets_no_tools`，锁住 orchestrator 的在线判定 |
| `docs/语音机器人需求文档/v2_migration_log/15_phase3_review_round4.md` | 更正 `52 passed` → `55 passed`；更正「只删预检仍绿」为「争锁超时」 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 16 |

## 3. 缺陷与修复

### 3.1 I-1：ack 阻塞期间撤销，ack 后仍广播上线

复现（审查者探针，修复前）：

```text
ALL STATUS EVENTS: [(True, ''), (False, '')]
LINK ready/closed/closing after: True True True
```

修复分两步：

1. **就绪时机前移**：`_send_hello_ack()` 返回后立刻 `session.ready = True`，不再
   等 `_mark_ready()` 拿锁/查库。否则设备刚收到 ack、服务端还要再等一次锁和一次
   DB 读，这段时间 `send()` 仍会返回 `device_offline`，属于「ack 之后不可控」的
   新窗口。`send()/get_status()` 仍检查 `closing/closed`，所以即使 ready 已置位，
   撤销后的链路依旧不可下发。
2. **上线广播加锁内复核**：

```python
async with device_lifecycle_lock(session.device_id):
    if get_device_link(session.device_id) is not session:
        return
    if session.closed or session.closing:
        return
    if not await self._device_is_active(session.device_id):
        await session.close(4401, "device_revoked")
        return       # offline 由 _teardown() 统一广播，避免重复
    await self._publish_status(session.device_id, online=True)
```

`closed/closing` 覆盖「撤销路由已拿到锁并关闭链路」；`is_active` 覆盖「撤销已提交
但 `DELETE` 还在等同一把锁」的窗口。offline 事件只由 `_teardown()` 发一次，
避免 `_mark_ready()` 与 `_teardown()` 各发一条。

### 3.2 M-1：App 网关不挡 `ready`

原条件只看 `getattr(link, "closing", False)`。ack 之前的链路 `closing=False`，于是
`remote.action.play` 会入队并回 `ok=true`，随后 worker 的 `link.send()` 才返回
`device_offline`。这与上一轮在 `closing` 分支刻意避免的「先 ok 再失败」自相矛盾。

修复：入口统一用 `device_link_is_online(link)`（存在 + ready + 未 closing/closed），
不满足即直接 `ack ok=false device_offline`；只有 `closing` 且 `remote.action.stop`
时才额外调用 `scheduler.stop()` 结算本地动作。

### 3.3 M-2：orchestrator 无红灯测试

新增 `test_registered_but_not_ready_link_gets_no_tools`：注册一个 `ready=False` 的
`FakeLink`，跑完整 `ChatOrchestrator.stream_chat`，断言模型首轮
`fake.calls[0]["tools"] is None`、system 提示包含「离线」、没有 `tool_result`、
链路没有收到任何命令。把 orchestrator 改回 `online=link is not None` 时该用例红。

### 3.4 M-3：日志 15 更正

- `52 passed` → `55 passed`（`f2affdbc` 时 `test_device_gateway.py` 35 +
  `test_app_gateway.py` 20）。
- 「只删预检则仍绿」不成立：删除预检后 `pause_on_get=2` 会落进 `_install()` 持锁的
  `get()`，与测试线程的真实 `DELETE` 争同一把生命周期锁；实测 5.87s 后
  `TimeoutError`（已加 5s 上界），不是「仍绿」。

### 3.5 M-4：让争锁死锁以失败结束

`test_revoke_after_precheck_is_caught_by_install_lock` 的 `client.delete(...)` 包进
`asyncio.wait_for(..., timeout=5.0)`，并在 `finally` 中 `factory.resume.set()`。
这样即使用例的暂停点因调用次数变化而落错位置，也会在 5 秒内失败，不会挂住整个
suite。`pause_on_get` 仍是次数驱动，但失败模式已可控；语义化暂停钩子留作后续改进。

### 3.6 M-5 / M-6

- 删除 `is_device_online(device_id)`：全仓库无调用方，保留只会让后来者绕过
  `device_link_is_online(link)` 重新引入「按 id 判断在线」的旧问题。
- `device_lifecycle_lock()` 每次调用时剔除已关闭的循环：

```python
stale = [lp for lp in list(_LIFECYCLE_LOCKS) if lp is not loop and lp.is_closed()]
for closed_loop in stale:
    del _LIFECYCLE_LOCKS[closed_loop]
```

这不能完全消除「value→key 环」在循环仍存活时的滞留，但能让测试/多循环场景在循环
关闭后回收；生产单进程单循环下等价于无操作。

## 4. 新增回归与有效性验证

| 用例 | 断言要点 |
| :--- | :--- |
| `test_revoke_while_ack_blocked_does_not_broadcast_online` | ack 阻塞期间真实撤销；ack 写成功后事件流中**没有** `online=true`，链路最终注销 |
| `test_not_ready_link_is_offline_without_dispatching_commands` | `ready=False` 的链路：遥控直接 `device_offline`，无命令下发 |
| `test_registered_but_not_ready_link_gets_no_tools` | `ready=False` 时 LLM 不注入 `robot.*`，system 提示离线，无工具调用 |
| `test_revoke_after_precheck_is_caught_by_install_lock`（改） | `DELETE` 有 5s 上界；核心守卫回退时 5.87s 内 `TimeoutError`，不再挂起 |

**红/绿证据**（临时脚本在内存中改回旧行为，`finally` 还原，工作区最终干净）：

```text
[I1: _mark_ready no reopen guard] exit=1
E       Left contains one more item: {'type': 'device.status', 'online': True, ...}
[I1 ...] restored

[M1: app gateway ignores ready] exit=1
E       assert True is False
[M1 ...] restored

[M2: orchestrator uses link is not None] exit=1
E       assert [...robot.action...] is None
[M2 ...] restored

[only drop handle precheck] exit=1 elapsed=7.4s
E       TimeoutError   (1 failed in 5.87s)
restored

MUTATIONS THAT STAYED GREEN: none
```

## 5. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
201 passed in 17.37s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 13.04s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
57 passed in 1.34s      # 36 + 21

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py
36 passed in 1.48s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_app_gateway.py
21 passed in 0.35s

$ & "...\companion-venv\Scripts\python.exe" C:\Users\ctr\AppData\Local\Temp\opencode\round10_mutation_check.py
3 个 mutation 全部 exit=1；MUTATIONS THAT STAYED GREEN: none

$ & "...\companion-venv\Scripts\python.exe" C:\Users\ctr\AppData\Local\Temp\opencode\round10_precheck_mutation.py
exit=1 elapsed=7.4s；1 failed in 5.87s；restored
```

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| `ready` 在 ack 返回后立即置位，而不是等 `_mark_ready()` | 否则 ack 与 ready 之间有「设备已收到 ack 但 `send()` 仍拒绝」的窗口 | 撤销后 ready 仍为 True，但 `send()/get_status()` 同时检查 closing/closed，不会误发 |
| `_mark_ready()` 锁内复核 `is_active` | 撤销路由不注销链路，仅 `close(4401)`；只查 current 拦不住 | 每次握手多一次 DB 读 |
| App 网关统一用 `device_link_is_online()` | 避免 `closing` 与 `ready` 两套条件各自漂移 | `remote.action.stop` 在未 ready 时不结算（此时也没有本地动作） |
| 保留次数驱动的 `pause_on_get`，只加 `wait_for` | 语义化钩子需要改测试工厂接口，收益小于本轮风险 | 以后在预检与 install 之间新增 `get()` 会让用例落错位置，但有 5s 上界、不会挂死 |
| 删除 `is_device_online()` | 无调用方，避免重复语义 | 若外部（不在本仓库）曾 import，会破坏兼容；当前无此用法 |
| 锁定循环的锁表按关闭循环清理，而非彻底弱引用 | `asyncio.Lock` 的 loop 强引用无法从 lock 侧切断 | 循环存活期间被竞争过的表项仍保留（单进程无影响） |

### 接受的风险（不修）

- **多 worker/多进程**：生命周期锁仍是进程内结构；`main.py` 单进程启动，部署多
  worker 时撤销 HTTP 可能落到不持有该 WS 的进程。属于既有设计约束。
- **`device.report` 在「落库后、广播前」撤销的毫秒级窗口**：仍可能先发一次运行态
  事件（日志 14 勘误已注明）。
- **模拟器未实现设备侧 >10 次/秒拒绝、`max_duration_ms` 看门狗、越界角度截断**。
- **e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408**（FakeChannel 已覆盖）。
- **上行 `session_id`/`turn_id` 文档歧义、`detail` 文案风格、单 worker 限制**。

## 7. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始；`device.report` 是否会让 App 把已 offline 设备重新显示为
  在线，需要 App 实现时确认。
- 真实 ESP32 固件是否容忍 ack 前的控制帧：服务端已用 `ready` 消除该窗口，仍需
  硬件验收。

## 8. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `f2affdbc..HEAD`。
3. 审查通过后进入音频 Block。
