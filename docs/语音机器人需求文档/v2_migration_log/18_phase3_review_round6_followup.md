# 18 · Phase 3 六次审查收尾：停机下线事件与测试定位

- 日期：2026-11-26
- 状态：✅ 完成（全量 205 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`7367dc15..53a3765f`（日志 17 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 审查结论

独立审查对 `53a3765f` 给出 **0 Critical / 0 Important / 6 Minor**，结论为
“With fixes（M-1、M-4 建议同 PR 或紧随其后，不阻断合并）”。审查确认：

- 撤销提交与 `close(4401)` 同锁后，原 Important 窗口在进程内闭合；
- 全仓库只有 5 处取 `device_lifecycle_lock`，全部是「生命周期锁 → DB」，未发现
  「DB 事务 → 生命周期锁」的反向路径，`require_app_token` 在路由前已提交；
- 404 分支在 `db.begin()` 内抛 `HTTPException` 时，事务回滚与锁释放都正确；
- 6 个 mutation（含额外「删掉撤销锁」）全部变红，工作区与副本 SHA256 一致；
- 日志 17 的 `204 / 16 / 60`、拆分 `39 + 21` 全部复现。

Minor：

| ID | 内容 | 本轮处置 |
| :--- | :--- | :--- |
| M-1 | `server_shutdown` 的 offline reason 实际不可达，协议/日志却宣称可用 | **修**（§3.1） |
| M-2 | 撤销在生命周期锁内提交+flush，尾延迟耦合（最坏 `busy_timeout` 15s） | 记录，不拆锁（§4） |
| M-3 | `handle()` 异常路径与撤销竞争时可能报成 `internal_error` | 记录，不修（§4） |
| M-4 | 三个新用例靠「第 3 次 `_device_is_active`」定位，回归只表现为超时 | **修**（§3.2） |
| M-5 | `assert not delete_task.done()` 证明力弱；`connected()` 的 ready 等待无诊断 | **修**（§3.2） |
| M-6 | 旧注释「hello_ack 在 `_install` 之前」与代码相反 | **修**（§3.3） |

## 2. 日志 17 勘误

日志 17 §3.4 的表格把「服务停机 → `server_shutdown`」写成已生效。实际在
`53a3765f` 时 `shutdown()` 的顺序是「`unregister_device_link()` → `close(1011,
"server_shutdown")`」，而 `close_reason` 只在 `_teardown()` 里被读取并广播；
`_teardown()` 稍后运行时 `was_current` 已为 False，不会发 offline。所以：

- 设备通道确实以 `1011 / server_shutdown` 关闭；
- App 侧**收不到** `device.status online=false`，协议 §8.2 的取值集合有一项不可达。

本轮已让 `shutdown()` 自己在锁内发这条事件，并补用例锁住。

## 3. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `shutdown()` 记录 `was_current` 与 `needs_offline`（进入前未关闭），注销后 close，若原本是当前链路则补发 `online=false, reason="server_shutdown"`；链路已被撤销/写失败关闭时只 close 不发，避免两条原因不同的 offline |
| `companion/server/tests/test_device_gateway.py` | 新增 `test_shutdown_broadcasts_offline_once`；新增 `_wait_mark_ready_entered()` 诊断助手；三条守卫用例改为「`hello_ack` 已发出」定位复核（不再数调用次数）；`Harness.connected()` 等待 ready 失败时抛出带链路状态/已发帧的 `AssertionError`；删除证明力弱的 `assert not delete_task.done()`；更正替换用例的旧注释 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 18 |

## 4. 修复细节

### 4.1 M-1：停机补发下线事件

```python
async with device_lifecycle_lock(session.device_id):
    await session.stop_writer()
    was_current = get_device_link(session.device_id) is session
    needs_offline = was_current and not session.closed
    if was_current:
        unregister_device_link(session.device_id, link=session)
        invalidate_device_scheduler(session.device_id)
    await session.close(1011, "server_shutdown")
    if needs_offline:
        await self._publish_status(
            session.device_id, online=False, reason="server_shutdown"
        )
```

- `needs_offline` 在 close 之前取：若链路已被撤销/`_safe_send` 关闭，offline 由
  那边的 `_teardown()` 发，这里再发会得到两条原因不同的事件。
- 事件在锁内发，与 `_mark_ready()` 的上线广播同锁，App 看到的 online/offline 顺序
  与撤销/替换一致。
- `_teardown()` 稍后运行时链路已注销，`was_current=False`，不会重复发。

### 4.2 M-4 / M-5：测试按上下文定位并提供诊断

```python
async def gated(device_id: str) -> bool:
    # 用「hello_ack 已发出」定位 `_mark_ready()` 的复核，而不是调用次数：
    # 预检与 `_install()` 都发生在 ack 之前。
    if not any(m.get("type") == "hello_ack" for m in channel.sent):
        return await real(device_id)
    ...
```

若将来 `_mark_ready()` 的复核被删除，`hello_ack` 之后不再调用 `_device_is_active`，
`_wait_mark_ready_entered()` 会抛：

```text
AssertionError: `_mark_ready()` 的复核未触发；sent=[...] link(ready,closing,closed)=...
```

而不是裸 `TimeoutError`。`Harness.connected()` 同理，握手未 ready 时直接给出链路
三元组与已发帧类型。

`assert not delete_task.done()` 已删除：`test_revoke_commit_waits_for_mark_ready_lock`
真正有效的是轮询 DB 仍为 `is_active=True`（审查的 mutation 4/6 也都先在那一行变红）。

### 4.3 M-6：注释与代码顺序一致

`test_second_connection_replaces_first` 的注释改为「注册（`_install`）早于
hello_ack；旧连接在替换流程内（新链路注册后）关闭」。

## 5. 回归与 mutation

| 用例 | 锁住什么 |
| :--- | :--- |
| `test_shutdown_broadcasts_offline_once` | 停机：通道 `(1011, "server_shutdown")`、链路注销、**恰好一条** `reason=server_shutdown` 的 offline |
| `test_mark_ready_keeps_link_offline_until_recheck_passes` | 复核被 gate 期间 `ready=False`、`send()` 回 `device_offline`、无命令下发；放行后无上线事件 |
| `test_mark_ready_skips_online_when_link_closes_during_recheck` | DB 读返回 True 后链路被 close：不得广播上线 |
| `test_revoke_commit_waits_for_mark_ready_lock` | 复核持锁期间 `DELETE` 的提交不生效 |

**红/绿证据**（临时脚本原地改写，`finally` 还原；工作区最终干净）：

```text
[ready set before _mark_ready] exit=1
E        +  where True = <DeviceSession>.ready
[ready set before _mark_ready] restored

[is_active branch removed] exit=1
E         Use -v to get more diff
[is_active branch removed] restored

[post-await closed check removed] exit=1
E         Use -v to get more diff
[post-await closed check removed] restored

[offline reason dropped] exit=1
E         - device_revoked
[offline reason dropped] restored

[revoke commit outside lifecycle lock] exit=1
E        +  where False = <DeviceModel>.is_active
[revoke commit outside lifecycle lock] restored

[shutdown offline event dropped] exit=1
E         Use -v to get more diff
[shutdown offline event dropped] restored

MUTATIONS THAT STAYED GREEN: none
```

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
205 passed in 18.06s
205 passed in 18.22s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 13.00s
16 passed in 12.88s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
61 passed in 2.13s      # 40 + 21
```

## 7. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 停机在锁内补发 offline，且仅在链路此前未关闭时发 | `_teardown()` 在 `shutdown()` 注销后不会补发；不补则 App 看不到下线 | 停机时 `_publish_status` 受 App 单次发送 2s 超时约束，已在锁内；但停机本就是有序关闭，可接受 |
| 新用例按「ack 已发出」而非调用次数定位复核 | 调用次数会随正常重构漂移，失败只表现为超时 | 依赖 `hello_ack` 一定先于 `_mark_ready()`；这正是当前握手顺序，且被其它用例覆盖 |
| M-2 / M-3 不修 | M-2 拆锁会复活刚修掉的撤销窗口；M-3 的异常路径若加生命周期锁，会与 `close()` 的 writer 冲刷形成「生命周期锁 vs writer 停止锁」反向顺序 | M-2：撤销在极端 DB 竞争下最坏等 `busy_timeout`（15s）；M-3：罕见竞态下 App 看到 `internal_error`，但 DB 已被撤销，设备重连会被 4401 拒绝 |

### 接受的风险（不修）

- **M-2 尾延迟**：撤销在生命周期锁内做一次 SQLite 提交（`busy_timeout=15000`）
  与一次 writer 冲刷（`1s + 1s` 上界），并可能等待同设备的 `_mark_ready()` 广播
  （App 单次发送 2s 上界）。正常实测 `test_revoke_commit_waits_for_mark_ready_lock`
  单次 0.6s 左右（其中 0.4s 是测试轮询）。要降低只能拆锁，会复活撤销窗口，
  因此不拆。
- **M-3 异常与撤销竞争**：谁先 `close()` 谁决定关闭码/`close_reason`；异常路径不取
  lifecycle lock（取锁会与 `stop_writer()` 的锁顺序反转）。DB 仍是权威，设备随后的
  重连会被 4401 拒绝。
- **多进程/多 worker**、**`device.report` 落库后广播前的窗口**、**模拟器未实现的
  设备侧限流/看门狗/角度截断**、**e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408**
  等既有项保持原状（见日志 17 §6）。

## 8. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 ack 前控制帧的容忍度仍需硬件验收。

## 9. 下一步

1. 提交本轮收尾修复并推送。
2. 派独立子智能体审查 `53a3765f..HEAD`（重点：停机 offline 的唯一性、测试定位改动是否
   削弱覆盖、M-2/M-3 的接受理由是否成立）。
3. 审查通过后进入音频 Block。

## 10. 勘误（日志 19 修订）

- **§3.1 / §7 的「停机补发 offline，App 能看到」只对进程内直接调用成立。** 真实
  uvicorn 先对连接发 1012、最后才跑 lifespan shutdown，`DeviceGateway.shutdown()`
  进入时 `_sessions` 已空；`server_shutdown` 在部署路径上仍不可达。日志 19 用
  `CompanionServer` 把应用停机提前到 uvicorn 关闭连接之前，并补了真实 uvicorn 的
  停机 e2e。
- **§7 的 M-3「不修理由：锁顺序反转」不成立。** `stop_writer()` 的
  `_writer_stop_lock` 在返回前已释放，不存在 `writer_stop → lifecycle` 的持锁边；
  异常路径可以安全地「生命周期锁 → close」。日志 19 已修复并补 `internal_error`
  用例。
- **§4.1 的 `needs_offline = was_current and not session.closed` 会漏发。** 撤销/
  异常路径先 `close()`、`_teardown()` 尚未拿锁时，`closed=True` 但链路仍是 current，
  旧判定会跳过唯一一条 offline。日志 19 改为「进锁时是 current 就发」，原因取
  `close_reason or "server_shutdown"`。
