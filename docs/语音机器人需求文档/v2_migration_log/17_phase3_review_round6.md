# 17 · Phase 3 六次审查修复：撤销提交串行化与 ready 置位后移

- 日期：2026-11-26
- 状态：✅ 完成（全量 204 passed；Phase 3 端到端 16 passed）
- 分支：`feature/companion-robot-v2`
- 审查范围：`f2affdbc..7367dc15`（`7367dc15` 即日志 16 的修复提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 目标与范围

第十一轮独立审查对 `7367dc15` 给出 **0 Critical / 1 Important / 6 Minor**，结论为
“With fixes”。Important 是本轮自己引入的窗口：

> 日志 16 把 `ready=True` 放在 `hello_ack` 之后、`_mark_ready()` 的生命周期复核
> 之前，理由是「避免 ack 与 ready 之间不可下发的窗口」。但撤销的 `close()` 要等
> 同一把生命周期锁，而 `_mark_ready()` 的 `is_active` 复核正持着锁——复核期间
> `closing` 仍为 False，`send()` 会把指令写到已撤销设备。

审查者用 `PausingSessionFactory(pause_on_get=4)` 卡在 `_mark_ready()` 的读上，真实
撤销已提交、`DELETE` 正在等锁，探针输出：

```text
PROBE2 SEND AFTER REVOKE COMMIT: ActionResult(status='done', detail='', action_id='')
PROBE2 SENT: ['hello_ack', 'idle.set']
```

即数据库已是 `is_active=False`，链路仍把 `idle.set` 写到了设备通道。App 的
`remote.*` 与 LLM 的 `robot.*` 都走同一个 `send()`。

其余 Minor：

- **M-1**：`_mark_ready()` 的 `is_active` 读与 `online=true` 广播之间仍有残窗
  （审查者探针复现 `[(True,''), (False,'')]`）。根因是撤销的 DB 提交在生命周期锁外。
- **M-2**：`closed/closing` 与 `is_active` 两道守卫没有被独立覆盖；`is_active` 撤销
  分支在旧用例里实际不可达（`DELETE` 的 `close()` 总是先命中 `closed`）。
- **M-3**：ack 阻塞撤销路径的 offline `reason` 是 `""`，与其它撤销路径的
  `device_revoked` 不一致。
- **M-4**：日志 16 的 Ruling「未 ready 时也没有本地动作」在链路替换窗口内不严格
  成立（旧链路的队列由 `old.close(4409)` 结算，不会挂到超时）。
- **M-5**：按循环分表的清理只是有界缓解；`WeakKeyDictionary` 非线程安全，多线程各跑
  loop 时可能有竞态（当前部署单 loop，未触发）。
- **M-6**：日志 16 的 5.87s 措辞与「mutation 全红」的可读性问题。

## 2. 改动文件清单

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/routers/devices.py` | 撤销的 DB 提交与 `close(4401)` **移入同一把 `device_lifecycle_lock`**；提交先于 close，锁释放时链路一定已 `closing/closed` |
| `companion/server/app/services/device_gateway.py` | `ready` 改回 `_mark_ready()` 锁内、`is_active` 复核**之后**置位；`is_active` 的 DB 读之后**再查一次** `closed/closing`；`DeviceSession.close_reason` 记录关闭原因，`_teardown()` 广播 offline 时复用 |
| `companion/server/app/services/app_gateway.py` | 无改动（上一轮已统一 `device_link_is_online`；M-4 只改文档） |
| `companion/server/tests/test_device_gateway.py` | `Harness.connected()` 等 `ready`；两处「看 ack 就下发」的用例改为等 `ready`；新增 3 条用例（见 §4）；ack 阻塞撤销用例补 offline 恰好一次 + reason 断言 |
| `companion/tests/test_phase3_e2e.py` | 更正注释：广播 `online=true` 的是 `hello_ack` 后的 `_mark_ready()`，不是 `_install()` |
| `companion/docs/protocol_v2.md` | §8.2 明确 `device.status` 离线 `reason` 的取值集合 |
| `docs/语音机器人需求文档/v2_migration_log/16_phase3_review_round5.md` | 追加勘误：`ready` 提前的 Ruling 被推翻、`is_active` 复核只堵一半、mutation 覆盖说明、5.87s 措辞 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 17 |

## 3. 缺陷与修复

### 3.1 I-1：撤销提交后、close 之前，链路仍可下发

修复分两层，缺一不可：

**① 撤销的提交与关闭串行化**（根因）：

```python
async with device_lifecycle_lock(device_id):
    async with db.begin():
        device = await db.get(DeviceModel, device_id)
        ...
        device.is_active = False
        device.token_hash = hash_token(generate_token())
        device.state = "revoked"

    link = get_device_link(device_id)
    if link is not None:
        await link.close(4401, "device_revoked")
```

`close()` 在第一个 `await` 之前同步置 `closing=True`，所以锁释放时链路一定已不可写。
`_mark_ready()` 拿到锁时只有两种结果：撤销尚未提交（正常上线），或链路已
`closing/closed`（不再上线）。

**② `ready` 后移到复核之后**：

```python
async with device_lifecycle_lock(session.device_id):
    if get_device_link(session.device_id) is not session:
        return
    if not await self._device_is_active(session.device_id):
        await session.close(4401, "device_revoked")
        return
    if session.closed or session.closing:   # DB 读是 await，期间可能被关闭
        return
    session.ready = True
    await self._publish_status(session.device_id, online=True)
```

代价是「设备已收到 ack、服务端复核完成前」的 `send()` 会短暂返回
`device_offline`（一次锁 + 一次 DB 读）。这比向已撤销设备下发指令安全，也是审查者
建议的修法。

### 3.2 M-1：`online=true` 广播与撤销提交的残窗

日志 16 只把 `is_active` 复核当作「降低概率」。本轮把提交移入锁后，`_mark_ready()`
的「读 is_active → 广播」与撤销的「提交 → close」互斥：要么先上线再撤销（设备在
上线时确实 active），要么直接不下发上线。**进程内窗口关闭。**

跨进程/直接改库仍可能绕过（锁是进程内的），`_mark_ready()` 的 `is_active` 复核是
唯一防线；这一条留在「接受的风险」。

### 3.3 M-2：三道守卫独立覆盖

| 守卫 | 用例 | 单独回退时 |
| :--- | :--- | :--- |
| `ready` 必须在复核后置位 | `test_mark_ready_keeps_link_offline_until_recheck_passes` | 复核暂停时 `assert link.ready is False` 立即红 |
| `is_active` 判定被忽略 | 同上 | 暂停期间/放行后都会广播上线，断言红 |
| DB 读期间链路被关闭 | `test_mark_ready_skips_online_when_link_closes_during_recheck` | 广播上线，断言红 |
| 撤销提交必须在锁内 | `test_revoke_commit_waits_for_mark_ready_lock` | 复核期间直接读到 `is_active=False`，断言红 |
| offline 只发一次且带 reason | `test_revoke_while_ack_blocked_does_not_broadcast_online` | reason 变 `""` / 数量不对，断言红 |

`is_active` 分支在进程内已与 `closed/closing` 部分重叠；它保留为**跨进程防御**，
用例通过 monkeypatch `_device_is_active` 让分支可达（不依赖第 N 次 `get()`）。
`test_mark_ready_skips_online_when_link_closes_during_recheck` 通过直接
`link.close()` 构造「链路已关闭但仍注册」的状态，覆盖 `closed/closing`。

> 最初还保留了「入口处先查 `closed/closing`」的快速路径，mutation 显示它恒绿
> （与 `is_active` + 读后复核完全冗余），已删除，只保留读后复核一处。

### 3.4 M-3：offline reason 统一

`DeviceSession` 新增 `close_reason`，在 `close()` 置 `closing` 后同步记录；
`_teardown()` 广播 `online=false` 时使用它。效果：

| 关闭来源 | 之前 | 现在 |
| :--- | :--- | :--- |
| 撤销（ack 阻塞 / 正常断开） | `""`（ack 阻塞路径） | `device_revoked` |
| 服务停机 | `""` | `server_shutdown` |
| 内部错误 | `""` | `internal_error` |
| 设备主动断开 | `""` | `""` |

协议 §8.2 同步列出取值集合。

### 3.5 M-4 / M-5 / M-6

- **M-4（文档）**：App 网关在 `ready=False` 时不调用 `scheduler.stop()` 是刻意的——此时
  新链路没有本地动作；旧链路的在途/排队任务由 `_install()` 的 `old.close(4409)` 结算，
  不会挂到 30s 超时。已在日志 16 的勘误与本文记录，代码不改。
- **M-5（不修）**：`device_lifecycle_lock()` 的「剔除已关闭循环」只在其它 loop 调用时
  回收，是缓解而非消环；`WeakKeyDictionary` 的线程安全前提是「一个 loop 一个线程」
  且调用串行。当前 `uvicorn.run` 单进程单 loop，不修；将来多线程多 loop 需改为把
  锁表挂在 loop 上或使用 `weakref.finalize`。
- **M-6（文档）**：日志 16 已追加勘误（5.87s → 约 5.9s / `CancelledError`；
  mutation 覆盖范围说明）。

## 4. 新增回归与有效性验证

| 用例 | 断言要点 |
| :--- | :--- |
| `test_mark_ready_keeps_link_offline_until_recheck_passes` | 复核暂停时 `ready=False`、`get_status().online=False`、`send()` 回 `device_offline`、无命令下发；放行后（复核返回 False）无上线事件、恰好一条 `device_revoked` offline |
| `test_mark_ready_skips_online_when_link_closes_during_recheck` | 复核的 DB 读期间直接 `link.close(4401)`：无上线事件、offline reason 正确、`ready` 保持 False |
| `test_revoke_commit_waits_for_mark_ready_lock` | `_mark_ready()` 持锁复核期间，`DELETE` 的提交不得生效（轮询 DB 仍 `is_active=True`）；放行后撤销完成、链路注销、无控制帧 |
| `test_revoke_while_ack_blocked_does_not_broadcast_online`（改） | 无上线事件；offline **恰好一条**且 `reason="device_revoked"`；通道关闭码 `(4401, "device_revoked")` |

**红/绿证据**（临时脚本原地改写并 `finally` 还原，工作区最终干净）：

```text
[ready set before _mark_ready] exit=1
E        +  where True = <DeviceSession>.ready
[ready set before _mark_ready] restored

[is_active branch removed] exit=1
E        Use -v to get more diff
[is_active branch removed] restored

[post-await closed check removed] exit=1
E        Use -v to get more diff
[post-await closed check removed] restored

[offline reason dropped] exit=1
E         - device_revoked
[offline reason dropped] restored

[revoke commit outside lifecycle lock] exit=1
E        +  where False = <DeviceModel>.is_active
[revoke commit outside lifecycle lock] restored

MUTATIONS THAT STAYED GREEN: none
```

## 5. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
204 passed in 18.52s
204 passed in 18.25s
204 passed in 18.34s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py
16 passed in 13.17s
16 passed in 13.08s
16 passed in 13.01s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py
60 passed in 1.99s      # 39 + 21
```

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 撤销的 DB 提交放进生命周期锁 | 只在锁内提交才能让「复核 is_active」与「撤销已提交」互斥；M-1 的残窗由此关闭 | 撤销 HTTP 会等待同设备的握手/换链/冲刷；锁内多一次 DB 提交。按设备独立，单设备延迟可接受 |
| `ready` 在复核后置位，接受 ack 后短暂的 `device_offline` | 安全性优先：复核未完成时不允许控制；一次锁 + 一次 DB 读的窗口通常 <1ms | 极端负载下 App 可能看到一次 `device_offline`；但不会向已撤销设备下发指令 |
| 删除入口的 `closed/closing` 快速判定 | mutation 恒绿，与「is_active + 读后复核」完全冗余 | 已关闭链路的握手会多一次 DB 读（仅握手期，一次） |
| `_teardown()` 统一用 `close_reason` 广播 | 撤销/停机/内部错误对 App 可区分；协议字段已有 | 依赖 `close()` 的原因；若上游只传空 reason，行为与旧版一致 |
| M-4 / M-5 只记录不修 | 旧链路队列由 `close(4409)` 结算；单 loop 部署下锁表竞态不可达 | 多线程多 loop 部署需重新设计锁表；替换窗口的终态事件略晚 |

### 接受的风险（不修）

- **多进程/多 worker**：撤销来自其它进程时，DB 提交不经过本进程的生命周期锁；
  `_mark_ready()` 的 `is_active` 复核仍会拒绝上线（若读到新值），但读与广播之间
  仍有毫秒级窗口。`main.py` 单进程启动。
- **`_device_is_active()` 读失败按「仍活跃」处理**：DB 抖动 + 恰好跨进程撤销时可能
  放行一次上线；这是既有取舍（避免 DB 抖动拒绝正常设备）。
- **`device.report` 在「落库后、广播前」撤销的毫秒级窗口**（日志 14 勘误）。
- **模拟器未实现设备侧 >10 次/秒拒绝、`max_duration_ms` 看门狗、越界角度截断**。
- **e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408**（FakeChannel 已覆盖）。
- **App（Kotlin）对 `device.status` 瞬时事件的处理**：未实现，无法验证。

## 7. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 ack 前控制帧的容忍度仍需硬件验收（服务端已用 `ready` 消除该
  窗口）。

## 8. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `7367dc15..HEAD`。
3. 审查通过后进入音频 Block。
