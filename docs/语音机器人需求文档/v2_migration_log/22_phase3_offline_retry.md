# 22 · Phase 3 停机（三）：取消后的 offline 补偿与测试隔离

- 日期：2026-11-26
- 状态：✅ 完成（全量 231 passed，连跑 2 次；e2e 18 passed；mutation 14/14 红、0 挂起）
- 分支：`feature/companion-robot-v2`
- 审查范围：`cd358a2f..9ca1bdfe`（日志 21 的提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 审查结论

独立审查对 `9ca1bdfe` 给出 **0 Critical / 1 Important / 2 Minor**，结论
“With fixes”。总预算（30.42s → 2.44s）、`force_exit` 中窗轮询、真实 TCP 1011
e2e、mutation 入库都被确认成立；Important 是取消后的 offline 可能永久丢失。

| ID | 内容 |
| :--- | :--- |
| I-1 | 取消落在 `unregister_device_link()` 之后时，会话虽被放回 `_sessions`，但重试的 `was_current` 已为 False，注册表也没有链路可查，`device.status online=false` 永远不会广播。`DeviceSession.close()` 此时也已置 `closed=True`，重试连传输层都不再关闭。`_teardown()` 的取消路径有同类窗口。 |
| M-1 | `test_lifespan_shutdown_is_bounded` 退出后把模块级 `ws.app_gateway` 留在 `_shutting_down=True`，M-3 只修了一半。 |
| M-2 | 该用例没有超时保护；mutation 把“挂起”直接记成 RED，无法区分“断言失败”和“没跑完”。 |

### 1.1 复现 I-1（同一探针，旧/新提交各跑一次）

探针把 `channel.close()` 换成永不返回（真实取消点就在 `DeviceSession.close()`
的 `await channel.close()`），确认链路已注销后取消 `shutdown()`，恢复 close 再跑
一次兜底：

```text
# 9ca1bdfe（修复前）
after unregister: link=None
after cancel: in_sessions= True pending= None offline_events= []
after retry : in_sessions= False pending= None offline_events= [] channel_closed= None

# 本轮（HEAD）
after unregister: link=None
after cancel: in_sessions= True pending= 'server_shutdown' offline_events= []
after retry : in_sessions= False pending= None offline_events= ['server_shutdown'] channel_closed= None
```

旧树上 App 永远收不到该设备的 `server_shutdown`；新树在重试时补发一次。注意
`channel_closed=None`：`close()` 在挂起的 `channel.close()` 之前已经把 `closed`
置位，重试不会再次关闭传输层，最终由 uvicorn 以 1012 断开（见 §6 的接受项）。

## 2. 修复

### 2.1 会话上的待发原因

`DeviceSession` 新增：

```python
# 已从注册表注销、但 offline 还没成功广播：停机/连接任务被取消时记下原因，
# 重试的 was_current 已是 False，只能靠这个字段补发。
self.pending_offline_reason: str | None = None
```

`shutdown()` 在锁内完成 `unregister` + `close()` 后置位，并在 `except BaseException`
中补记（取消可能发生在 `close()` 内部，赋值行还没执行）。`_teardown()` 用同一字段
覆盖“uvicorn drain 超时取消连接任务”的路径，并在取消时把会话放回 `_sessions`：

```python
except BaseException:
    if was_current and session.pending_offline_reason is None:
        session.pending_offline_reason = session.close_reason
    if session.pending_offline_reason is not None or not session.closed:
        self._sessions.add(session)
    raise
```

### 2.2 单点广播：先清空、再 `await`

```python
reason = session.pending_offline_reason
if reason is None:
    return
session.pending_offline_reason = None          # 先清空，避免并发重复
if get_device_link(session.device_id) is not None:
    return                                     # 已被新链路接管，丢弃过期 offline
try:
    await self._publish_status(..., online=False, reason=reason)
except BaseException:
    # 第二轮审查后加了 `is None` 守卫：不覆盖期间写入的更新原因。
    if session.pending_offline_reason is None:
        session.pending_offline_reason = reason  # 取消：写回 + 放回集合重试
    self._sessions.add(session)
    raise
```

**这条“先清空”不是洁癖，是真实回归换来的。** 第一版实现是“广播返回后再清空”，
全量 e2e 立刻变成：

```text
assert [True, False, False] == [True, False]
Left contains one more item: False
```

原因：`shutdown()` 广播时读循环因链路关闭退出，`_teardown()` 并发跑到
`_publish_pending_offline()`，看到同一个待发原因又发了一条。改为 `await` 前清空后
只有一条；广播被取消时写回重试，语义变成 **at-least-once**。

### 2.3 `_teardown()` 对称处理

`_teardown()` 原先没有取消保护：uvicorn 在 `timeout_graceful_shutdown=20` 之后会
取消连接任务，若取消落在它 `unregister` 之后、`channel.close()` 期间，会话既不在
注册表也不在 `_sessions`，lifespan 兜底看不到它。现在取消时按 §2.1 放回集合，由
停机兜底完成关闭 + 广播。

## 3. 其余两条 Minor

| ID | 处理 |
| :--- | :--- |
| M-1 | `test_lifespan_shutdown_is_bounded` 的 `try/finally` 里调用两个网关的 `open_for_connections()`；探针确认退出后 `device False app False`。 |
| M-2 | 该用例改用 `asyncio.wait_for(run_lifespan(), timeout=3.0)` 包裹；mutation 脚本把 `TimeoutExpired` 记为 `TIMEOUT -> HARNESS FAILURE`，单独计数并让脚本非零退出，不再与正常 RED 混在一起。 |

## 4. 改动文件清单

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/device_gateway.py` | `pending_offline_reason`；`_publish_pending_offline()`；`shutdown()` / `_teardown()` 取消补偿 |
| `companion/server/tests/test_device_gateway.py` | 新增取消落在 unregister 之后、`_teardown()` 第二次取消、并发不重复广播 3 例（46 → 49） |
| `companion/server/tests/test_server.py` | lifespan 兜底用例加 3s 超时与 `try/finally` 复位（12 例不变） |
| `companion/tests/mutation/round15_shutdown.py` | 新增 3 个变体（11 → 14）；TIMEOUT 单独计为 harness failure |
| `companion/docs/protocol_v2.md` | §8.2 补 offline 的 at-least-once 与重试语义 |
| `docs/语音机器人需求文档/v2_migration_log/21_phase3_total_shutdown_budget.md` | 追加 §10 勘误 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 22 |

## 5. 新增用例

| 用例 | 锁住什么 |
| :--- | :--- |
| `test_shutdown_cancelled_after_unregister_still_publishes_offline` | 取消越过 unregister 后，重试仍补发 `server_shutdown`（I-1） |
| `test_teardown_cancelled_after_unregister_keeps_offline_pending` | uvicorn 第二次取消连接任务后，会话回集合、offline 可补发（I-1） |
| `test_pending_offline_not_republished_by_concurrent_teardown` | 广播进行中并发 `_teardown()` 只能发一条 offline（本轮新发现的重复缺陷） |

对旧提交 `9ca1bdfe` 复跑时，第一条先因 `DeviceSession` 没有
`pending_offline_reason` 抛 `AttributeError`，去掉该新字段断言后仍以
`assert [] == ['server_shutdown']` 变红；第二条以
`assert <DeviceSession> in set()` 变红，证明 I-1 真实存在。第三条在旧树上
**通过**——它锁的是本轮第一版实现（广播后才清空待发原因）的重复缺陷，不是
9ca1bdfe 的回归。日志里按“2 红 1 绿”记录，避免以后误读。

## 6. Ruling 与接受的风险

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| offline 采用 at-least-once，而不是“严格一次” | 总线没有投递确认；取消可能发生在已投递之后 | App 可能收到重复的 `online=false`（状态幂等） |
| 先清空待发原因再广播 | 防止并发 `_teardown()` 重复广播；这是 e2e 实测出来的 | 若在总线上已投递后被取消，重试会再发一次 |
| 重试不再调用 `channel.close()` | `DeviceSession.close()` 在挂起的 `channel.close()` 前已置 `closed=True`，重试是空操作；强行重开会让 `close()` 的幂等语义失效 | 该设备看到 1012 而不是 1011（uvicorn 强制断开） |
| `_teardown()` 取消也回填 `_sessions` | 否则 drain 超时取消会同时从集合和注册表里丢掉设备 | 兜底可能多处理一条已关闭会话（`close()` 幂等） |
| 有待发原因但已有新链路时丢弃 | 停机标志解除后设备重连，上线事件由新链路负责 | best-effort：检查与投递之间仍可能插入新链路，旧 offline 可能晚于 online 送达 |
| mutation 的 TIMEOUT 计为 harness failure | 挂起说明测试失去快速失败保护，不是干净的 RED | 脚本在本机无 pytest-timeout 时也能区分两类失败 |

### 接受的风险（不修）

- 本进程内 offline 恰好一次；跨重试/取消是 at-least-once，接收方需容忍重复。
- 取消发生在 `channel.close()` 时，传输层最终由 uvicorn 以 1012 断开，设备可能
  看到 1012 而不是协议约定的 1011（预算超时路径）。
- `test_phase3_shutdown_e2e.py` 与 `test_phase3_app_gate_e2e.py` 仍依赖真实端口与
  线程，未做跨平台 CI 验证（Windows 本机通过）。
- 既有保留项不变：`device.report` 落库后广播前窗口、模拟器未实现的设备侧限流/
  看门狗/角度截断、e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408 等。
- Android App 对 1011/1012 与重复 offline 的处理未验证（App 未实现）。

## 7. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
231 passed in 20.10s
231 passed in 19.65s

$ & "...\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py   # ×5
49 passed in 2.05s
49 passed in 2.08s
49 passed in 2.03s
49 passed in 2.07s
49 passed in 2.06s

$ & "...\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py \
    companion/tests/test_phase3_shutdown_e2e.py companion/tests/test_phase3_app_gate_e2e.py
18 passed in 13.45s

$ & "...\python.exe" companion/tests/mutation/round15_shutdown.py
MUTATIONS THAT STAYED GREEN: none
MUTATIONS THAT HUNG: none

$ & "...\python.exe" round15_probe.py bound      # 预算未回归
application shutdown exceeded 0.2s, falling back to uvicorn connection cleanup
lifespan shutdown exceeded 2.0s, falling back to uvicorn connection cleanup
elapsed=2.42s alive=False device_shutdown_calls=2

$ & "...\python.exe" round15_probe.py force
force exit: abandoning application shutdown
elapsed_after_force=0.14s
```

Mutation 清单（14 个，全部 `exit=1`）：

```text
app close in finally has no budget
lifespan helper ignores its timeout
force_exit is not polled mid-window
application deadline ignores the configured value
reload branch removed
object app accepted with reload
graceful drain timeout not configured
lifespan does not reset app gateway flag
cancelled device session is not put back
cancelled shutdown forgets the pending offline reason        ← 新增
teardown cancellation drops the session                      ← 新增
pending offline is never cleared                             ← 新增
app gateway deadline ignores its timeout
app gateway accepts connections during shutdown
```

## 8. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 1011/1012、重复 `online=false` 的容忍度仍需硬件验收。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `9ca1bdfe..HEAD`（重点：待发原因的并发/取消语义、
   `_teardown()` 回填是否引入重复处理、at-least-once 的文档一致性、新用例是否
   真正锁住旧实现）。
3. 审查通过后进入音频 Block。

## 10. 第二轮审查（`d4b2df1f`）与微调

独立审查对 `d4b2df1f` 给出 **0 Critical / 0 Important / 4 Minor**，结论
“Yes”（建议合并前顺手修 M-1/M-2）。核心修复（待发原因、`_teardown` 回填、
先清空再 `await`、预算不变）被确认成立；4 条都在测试/注释质量：

| ID | 处理 |
| :--- | :--- |
| M-1 | 并发用例的 `gated_publish()` 只拦第一条 offline；第二条直接落库，使 “pending offline is never cleared” 变体命中 `assert count == 1` 而不是 3s 超时。`finally` 同时取消未完成的 handle task。 |
| M-2 | 取消后的中间断言（会话在集合里、原因为 `server_shutdown`）已删除：它们取决于取消的 handle 是否已被调度，不属于契约；只保留最终态（offline 恰好一条、`pending is None`）。 |
| M-3 | `_publish_pending_offline()` 的注释改为 best-effort，承认“检查与逐订阅者投递之间仍可能插入新链路”；取消写回改为 `if ... is None` 的防御写法。 |
| M-4 | 日志 21 §10 补一条：§7（及 §2.5）的“最多丢失”仅指预算耗尽后的 1012 收尾，重试路径已是 at-least-once。 |

同时修正 §5 的表述：3 个新用例在旧树上是 **2 红 1 绿**，第三条锁的是本轮第一版
实现的重复缺陷，而不是 `9ca1bdfe` 的回归（旧树实测输出见审查记录）。

本轮不新增用例，数量仍为 231；`round15_shutdown.py` 仍为 14 个变体。

**增量复审（`300792c3`）**：结论 Yes，仅 3 条文档/注释措辞——把 §10 里错误的
“§8/§9”引用改为 §7（及 §2.5）；把“窗口在引入待发原因之前就存在”改为“把广播移出
生命周期锁后才出现”；同步 §2.2 代码片段的 `is None` 守卫与 §6 的 best-effort
限定。无代码行为变化。
