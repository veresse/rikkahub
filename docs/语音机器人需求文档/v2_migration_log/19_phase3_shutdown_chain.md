# 19 · Phase 3 停机链路：在 uvicorn 关闭连接前下线

- 日期：2026-11-26
- 状态：✅ 完成（全量 211 passed；含真实 `CompanionServer` 停机 e2e）
- 分支：`feature/companion-robot-v2`
- 审查范围：`53a3765f..a6f221ab`（日志 18 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 审查结论

独立审查对 `a6f221ab` 给出 **0 Critical / 1 Important / 6 Minor**，结论为
“With fixes”。Important 是决定性的：

> 日志 18 宣称「`server_shutdown` 现在可达」，但只在**进程内直接调用**
> `DeviceGateway.shutdown()` 时成立。真实 uvicorn 的 `Server.shutdown()` 先对所有
> 连接发 1012 并等待连接清空，**最后**才跑 lifespan shutdown（uvicorn 0.54.0
> `server.py:215-245`）。等 lifespan 调网关时 `_sessions` 已空，App 的 `/app/ws`
> 也已被断开，新事件依然没有接收者。

审查者用真 uvicorn 探针复现：

```text
[GW] publish <id> online=True  reason=''
[GW] teardown() ENTER <id> closed=False closing=False current=True
[GW] publish <id> online=False reason=''
[GW] shutdown() ENTER sessions=0
[probe] app device.status events = [(True, '')]
[probe] app ws close = 1012 ''
[probe] device ws close = 1012 ''
```

其余 Minor：

| ID | 内容 |
| :--- | :--- |
| M-1 | `needs_offline = was_current and not session.closed` 会漏掉「撤销/异常已 close、`_teardown()` 尚未拿锁」的唯一一条 offline |
| M-2 | 新用例没覆盖 `was_current` / `not closed` 两条分支（改成恒 True / 加 `closing` 都仍 40 passed） |
| M-3 | 日志 18 的 M-3「不修理由（锁顺序反转）」不成立：`stop_writer()` 的 `_writer_stop_lock` 在返回前已释放，不存在 `writer_stop → lifecycle` 的持锁边 |
| M-4 | `shutdown()` 先拍快照再 `clear()`，会漏掉快照之后才完成 `_install()` 的会话 |
| M-5 | offline 在锁内逐设备发送，停机总时长随设备数线性增长；循环无 `try/finally` |
| M-6 | 测试诊断两处盲区：`_mark_ready()` 移到 ack 之前时提示错误；`Harness.connected()` 快照链路引用 |

## 2. 日志 18 勘误

- **§3.1 / §7 的「停机补发 offline，App 能看到」只对进程内直接调用成立。**
  uvicorn 部署路径上 lifespan 晚于连接关闭，`server_shutdown` 仍不可达；本轮用
  `CompanionServer` 修掉，并新增真 uvicorn 停机 e2e。
- **§7 的 M-3 理由（锁顺序反转）不成立。** `stop_writer()` 获取的
  `_writer_stop_lock` 在函数返回时已释放，等待的是 writer 任务与 `_flush_reports()`
  （只碰 DB/总线，不取生命周期锁）。因此异常路径可以安全地「生命周期锁 → close
  → writer 停止锁」。本轮已按该顺序修复，并补 `internal_error` 用例。

## 3. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/server.py` | `CompanionServer(uvicorn.Server)`：在 `super().shutdown()` 之前执行「设备下线 → `drain_background_tasks()` → 关闭 App」，lifespan 保留为兜底 |
| `companion/tests/test_phase3_shutdown_e2e.py` | 真实 `CompanionServer.run()`（独立线程 + 循环）：App 先收到 `(False, "server_shutdown")`，自身与设备都以 `1011 / server_shutdown` 关闭 |
| `docs/语音机器人需求文档/v2_migration_log/19_phase3_shutdown_chain.md` | 本日志 |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/main.py` | lifespan 启动时 `device_gateway.open_for_connections()` 复位停机标志；`__main__` 改用 `uvicorn.Config` + `CompanionServer(config).run()` |
| `companion/server/app/services/device_gateway.py` | `_shutting_down` 标志（`_install()` / `_mark_ready()` 锁内复核）；`shutdown()` 改为「置标志 → 逐个 pop 处理 → 锁外统一广播」，offline 原因取 `close_reason or "server_shutdown"`；`handle()` 异常路径在生命周期锁内 `close(1011, "internal_error")` |
| `companion/server/tests/test_device_gateway.py` | 新增 5 条用例（见 §4）；`_wait_mark_ready_entered()` 在链路已 ready 时提示「复核可能在 ack 之前执行」；`Harness.connected()` 每轮读取当前链路 |
| `docs/语音机器人需求文档/v2_migration_log/18_phase3_review_round6_followup.md` | 追加勘误（`server_shutdown` 不可达、M-3 理由不成立） |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 19 |
| `companion/docs/protocol_v2.md` | §8.2 注明 `server_shutdown` 由优雅停机在关闭连接前广播，原因可能保留先到的 `device_revoked` / `internal_error` |

## 4. 修复细节

### 4.1 I-1：停机必须先于 uvicorn 关闭连接

```python
class CompanionServer(uvicorn.Server):
    async def shutdown(self, sockets=None) -> None:
        from companion.server.app.routers import ws
        from companion.server.app.services.events import drain_background_tasks

        try:
            await ws.device_gateway.shutdown()
            await drain_background_tasks()
        finally:
            await ws.app_gateway.shutdown()
        await super().shutdown(sockets)
```

`main.py` 的 `__main__` 从 `uvicorn.run(...)` 改为构造 `uvicorn.Config` 并由
`CompanionServer(config).run()` 启动（部署脚本 `companion/deploy/companion.service`
即 `python -m companion.server.app.main`，自动走这条路径）。lifespan 里的
`device_gateway.shutdown()` / `app_gateway.shutdown()` 仍在，作为其它 ASGI 宿主或
直接调用时的兜底（第二次调用为空集，幂等）。

e2e 断言（真实 uvicorn）：

```text
[device.status 事件] [True, False]，最后一条 reason=server_shutdown
[app ws close]       (1011, "server_shutdown")
[device ws close]    (1011, "server_shutdown")
```

### 4.2 M-1 / M-5：原因取 `close_reason`，广播移出锁

```python
self._shutting_down = True
offline: list[tuple[str, str]] = []
while self._sessions:
    session = self._sessions.pop()
    async with device_lifecycle_lock(session.device_id):
        await session.stop_writer()
        was_current = get_device_link(session.device_id) is session
        if was_current:
            unregister_device_link(...)
            invalidate_device_scheduler(...)
        await session.close(1011, "server_shutdown")
        if was_current:
            offline.append(
                (session.device_id, session.close_reason or "server_shutdown")
            )
for device_id, reason in offline:
    await self._publish_status(device_id, online=False, reason=reason)
```

- 只要进锁时仍是 current 就发一条：即便链路已被撤销/异常路径关闭（`close()` 空转），
  `close_reason` 也保留先到的 `device_revoked` / `internal_error`，不会丢事件。
- `_safe_send()` 失败只置 `closing`（保留冲刷能力），所以不能把 `closing` 当作已下线。
- 广播在锁外统一发：慢 App 的单次发送超时不再叠加进每设备锁的持有时间。总量仍是
  `O(设备数 × 单次发送)`，属于停机可接受范围（见 §7）。

### 4.3 M-4：停机后不再注册新链路

```python
# _install()，锁内、DB 读之后
if self._shutting_down:
    await session.close(1011, "server_shutdown")
    return False
```

配合 `shutdown()` 的 pop 循环：即使 `_install()` 在停机取快照之后才完成，
`shutdown()` 也会在下一轮把它取到；已经通过 flag 检查、正在注册的会话也会被下一轮
处理。`_mark_ready()` 在锁内再查一次 flag，避免停机开始后广播上线。
`open_for_connections()` 在 lifespan 启动时复位标志，避免同进程重启（测试/嵌入式）
被上一次停机永久拒绝。

### 4.4 M-3：异常路径进锁

```python
if session is not None:
    async with device_lifecycle_lock(device_id):
        await session.close(1011, "internal_error")
```

锁顺序统一为「生命周期锁 → `_close_lock` → writer 停止锁」，与撤销/停机一致，无环。
与撤销竞争时谁先进锁谁决定关闭码；DB 仍是权威（设备重连会被 4401 拒绝）。

### 4.5 M-2 / M-6：新增用例与诊断

| 用例 | 锁住什么 |
| :--- | :--- |
| `test_shutdown_after_failed_send_reports_offline_once` | 写失败 `closing=True, closed=False` 后停机：仍恰好一条 `server_shutdown`（不能把 `closing` 算作已关闭） |
| `test_shutdown_reports_reason_of_already_closed_link` | 已 `close(4401)` 但仍注册（handle 不退出的窗口）：offline 保留 `device_revoked`，不能漏发 |
| `test_shutdown_rejects_install_in_flight` | `_install()` 卡在停机之前：停机后注册被拒绝、通道 1011、无上线事件 |
| `test_mark_ready_does_not_publish_online_after_shutdown_started` | 复核持锁期间开始停机：放行后不得广播上线 |
| `test_read_loop_error_closes_with_internal_error` | 读循环异常：`(1011, "internal_error")` + 一条 `internal_error` offline |

`_wait_mark_ready_entered()` 在超时时若发现 `link.ready=True`，会提示「复核可能在
hello_ack 之前就执行了」；`Harness.connected()` 改为每轮读取当前 `get_device_link()`。

## 5. Mutation（副本/原地改写，`finally` 还原，工作区最终干净）

```text
RED  ready set before _mark_ready
RED  is_active branch removed
RED  post-await closed check removed
RED  offline reason dropped
RED  revoke commit outside lifecycle lock
RED  shutdown offline event dropped
RED  shutdown skips already-closed link
RED  shutdown always reports server_shutdown
RED  install accepted after shutdown started
RED  mark_ready publishes online after shutdown started
RED  CompanionServer runs app shutdown after uvicorn
MUTATIONS THAT STAYED GREEN: none
```

最后一条在**真实 uvicorn e2e** 上变红（App 收不到 offline、关闭码变 1012），证明
I-1 的修复不是只靠单元测试自证。

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
211 passed in 18.73s
211 passed in 18.48s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py companion/tests/test_phase3_shutdown_e2e.py
17 passed in 13.13s
17 passed in 13.22s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
66 passed in 2.09s      # 45 + 21

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_shutdown_e2e.py
1 passed in 1.54s
```

## 7. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 用 `CompanionServer` 覆盖 `uvicorn.Server.shutdown` | lifespan 在连接关闭之后才执行，唯一能在 App 断开前广播的窗口就是 uvicorn 关闭连接之前 | 停机时先跑应用关闭，耗时不计入 uvicorn 的 `timeout_graceful_shutdown`；总量见下 |
| 停机标志 + pop 循环 + `_mark_ready()` 复核 | 快照与 `clear()` 会漏掉并发注册的链路；停机后广播上线会误导 App | 停机窗口内的新握手会被 1011 拒绝（符合预期） |
| offline 原因取 `close_reason or "server_shutdown"` | 撤销/异常先关闭时保留真实原因；不再用 `closed` 判断是否已下线（会漏 `closing` 与「已关闭未 teardown」） | App 在停机时可能收到 `device_revoked`，这是事实本身 |
| 异常路径也在生命周期锁内关闭 | 与撤销/停机互斥，避免「撤销已提交却报 internal_error」的乱序；锁顺序经审查确认无环 | 异常处理多等一次锁/一次冲刷（≤2s），发生在连接已出错之后 |
| 不把 offline 广播放进 `try/finally` | 循环内只做 close/注销；广播移到循环之后，异常会直接冒泡给 uvicorn（进程即将退出） | 极端情况下最后一次停机事件可能丢失，但不会卡住进程 |

### 接受的风险（不修）

- **停机总时长线性增长**：N 台设备 ×（writer 冲刷 ≤2s + App 单次发送 ≤2s）。慢而
  不死的 App 会在每台设备上累计；彻底断开的 App 在第一次超时后即被 `_detach`。
  这是「先广播再关 App」的必然代价。
- **多 worker/多进程**：`CompanionServer` 的停机是每进程各自执行；跨进程撤销仍是
  进程内锁，`_mark_ready()` 的 `is_active` 复核是唯一防线。
- **`device.report` 落库后广播前的窗口**、**模拟器未实现的设备侧限流/看门狗/角度
  截断**、**e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408** 等既有项保持原状。
- **App 端对 `server_shutdown` / 1011 的处理**：Android App 未实现，无法验证。

## 8. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 ack 前控制帧的容忍度仍需硬件验收。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `a6f221ab..HEAD`（重点：`CompanionServer` 覆盖是否与
   reload/graceful timeout 兼容、停机标志的生命周期、offline 唯一性）。
3. 审查通过后进入音频 Block。

## 10. 勘误（日志 20 修订）

- **§4.1 / §7 的「App 通道以 1011 关闭」不覆盖停机窗口内新到达的连接。** 旧实现
  对会话集合拍快照后 `clear()`，晚到的 App 会被 uvicorn 以 1012 收尾。日志 20 给
  `AppGateway` 加了对称的 `_shutting_down`（新连接直接 1011，lifespan 启动复位）。
- **§7 的「writer 冲刷 ≤2s」低估了停机时长。** 2s 只覆盖 `stop_writer()` 的等待，
  不覆盖取消后 `_flush_reports()` 的写库（`busy_timeout=15000`）。日志 20 给应用层
  停机加了 10s 总时限，超时后交给 uvicorn 的 1012 收尾，并让 `force_exit` 直接跳过。
- **§5 的「shutdown skips already-closed link → RED」只对窄变体成立。** 断言原本写在
  `task.cancel()` 之后，取消 `handle()` 触发的 `_teardown()` 会补发同一条 offline；
  把 `shutdown()` 改成「已 closed 就 `continue`」仍为 GREEN。日志 20 把断言移到取消
  之前，该变体现在为 RED。
- **§4.1 的 `CompanionServer(config).run()` 不启动 reloader。** `reload=settings.DEBUG`
  被静默忽略（`Server.run()` 没有 reload 分支），日志 20 改为 `run(config)` 照抄
  `uvicorn.run()` 的 `ChangeReload` 分支。
- **§4.1 的保证只在 `python -m` / `CompanionServer` 入口成立**，直接
  `uvicorn ...:app` 仍不可达；已在协议 §8.2 与 `companion/README.md` 写明。
