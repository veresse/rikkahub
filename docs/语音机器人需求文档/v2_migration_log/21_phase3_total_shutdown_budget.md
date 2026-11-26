# 21 · Phase 3 停机总预算：两层时限、force_exit 与连接 drain

- 日期：2026-11-26
- 状态：✅ 完成（全量 228 passed；e2e 18 passed；11 个 mutation 全红）
- 分支：`feature/companion-robot-v2`
- 审查范围：`cd358a2f`（日志 20 的提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 审查结论

独立审查对 `cd358a2f` 给出 **0 Critical / 1 Important / 6 Minor**，结论为
“With fixes”。reload 分支、`finally` 收尾、App 门控、M-1 强化都被确认成立；
Important 是日志 20 对“总时限”的描述与实现不符。

| ID | 内容 |
| :--- | :--- |
| I-1 | 10s 只约束 `CompanionServer` 的第一次应用层关闭；lifespan 兜底会**再无条件跑一遍**无界的 `device_gateway.shutdown()`，`AppSession.close()` 也没有超时，uvicorn 1012 之后的连接 drain 仍是 `None`（无界）。日志 20 §2.2 的“10s + 关闭 App 通道，不会无限增长”不成立。 |
| M-1 | `force_exit` 只在 `shutdown()` 入口检查；第二次 Ctrl+C 落在 10s 窗口内不会生效。 |
| M-2 | `run()` 缺 uds 清理与“reload/workers + 非字符串 app”的显式拒绝。 |
| M-3 | `test_lifespan_resets_shutdown_flags` 退出后把模块级网关留在 `_shutting_down=True`。 |
| M-4 | `except asyncio.TimeoutError` 在 3.11 会误吞应用层内部抛出的 `TimeoutError`。 |
| M-5 | 停机窗口内新 `/app/ws` 的 1011 只有 FakeChannel 单测。 |
| M-6 | mutation 脚本不在仓库，审查无法复核。 |

### 1.1 复现 I-1（同一探针，旧/新提交各跑一次）

探针（`round15_probe.py`）把 `ws.device_gateway.shutdown` 换成 sleep 30s，令
`APP_SHUTDOWN_TIMEOUT_SECONDS=0.2`，启动真实 `CompanionServer` 后触发
`should_exit`。旧提交 `cd358a2f` 在临时 worktree 中运行：

```text
# cd358a2f（修复前）
application shutdown exceeded 0.2s, falling back to uvicorn connection cleanup
elapsed=30.41s alive=False device_shutdown_calls=2

# 本轮（修复后）
application shutdown exceeded 0.2s, falling back to uvicorn connection cleanup
lifespan shutdown exceeded 2.0s, falling back to uvicorn connection cleanup
elapsed=2.39s alive=False device_shutdown_calls=2
```

`device_shutdown_calls=2` 说明 lifespan 仍会重试一次（这是有意的兜底），但重试被
`LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS=2.0` 截断，所以总时长从 30.41s 降到 2.39s。
审查自己用 3s 慢关闭测得 `elapsed=3.41s`，趋势一致。

### 1.2 复现 M-1（同一探针）

`_shutdown_application` 改成 sleep 30s、`APP_SHUTDOWN_TIMEOUT_SECONDS=5`，在停机
开始 0.5s 后置 `force_exit=True`：

```text
# cd358a2f（修复前）
application shutdown exceeded 5.0s, falling back to uvicorn connection cleanup
elapsed_after_force=4.61s

# 本轮（修复后）
force exit: abandoning application shutdown
elapsed_after_force=0.16s
```

## 2. 修复与决策

### 2.1 单一停机编排模块：`services/application_shutdown.py`

顺序仍是「设备下线 → 等广播 → 关 App」，但把预算显式化：

```python
APP_SHUTDOWN_TIMEOUT_SECONDS = 10.0        # CompanionServer 第一次
LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS = 2.0    # lifespan 兜底
APP_CLOSE_TIMEOUT_SECONDS = 2.0            # finally 里关 App 的独立预算
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 20.0   # uvicorn 1012 之后的 drain 上限

async def shutdown_application() -> None:
    try:
        await ws.device_gateway.shutdown()
        await drain_background_tasks()
    finally:
        await ws.app_gateway.shutdown(timeout=APP_CLOSE_TIMEOUT_SECONDS)
```

`bounded_shutdown_application(timeout, source=...)` 用「task + `asyncio.wait`」而不是
`wait_for`：超时后先记录、再取消任务并 `gather`，让 `finally` 的 App 关闭跑完；调用方
被取消时也在 `finally` 里取消/回收应用层任务，不留下 pending task。

### 2.2 `CompanionServer`：截止时间 + 0.1s 轮询 `force_exit`

```python
task = asyncio.ensure_future(self._shutdown_application())
deadline = loop.time() + APP_SHUTDOWN_TIMEOUT_SECONDS
while True:
    remaining = deadline - loop.time()
    if remaining <= 0:
        logger.warning("application shutdown exceeded %.1fs ...")
        break
    done, _ = await asyncio.wait(
        {task}, timeout=min(remaining, FORCE_EXIT_POLL_SECONDS)
    )
    if task in done:
        break
    if self.force_exit:
        logger.warning("force exit: abandoning application shutdown")
        break
```

不再用 `except asyncio.TimeoutError` 判断超时（M-4）：是否超时由截止时间循环决定；
任务以普通异常结束时统一 `logger.error(..., exc_info=error)`，不会被误报成超时。

### 2.3 lifespan 兜底也有界

`main.lifespan` 的 `yield` 之后改为：

```python
await bounded_shutdown_application(
    LIFESPAN_SHUTDOWN_TIMEOUT_SECONDS, source="lifespan shutdown"
)
```

正常路径下第一次已经清空两个网关，这里是近似空操作；第一次超时/`force_exit` 跳过时
最多再花 2s + 2s（App 关闭），而不是重新无界地跑设备关闭。

### 2.4 uvicorn 连接 drain：`timeout_graceful_shutdown=20`

`main.py` 抽出 `build_config()`（便于测试），加入
`timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS`。这样 1012 之后
`_wait_tasks_to_complete()` 也有上限，不会因为某个连接任务的 `_teardown` 卡在
SQLite（`busy_timeout=15000`）而无界。

停机上界（单进程）：

| 阶段 | 上限 |
| :--- | :--- |
| `CompanionServer` 应用层关闭 | 10s |
| 取消后 `finally` 关 App（每个 `close` 受剩余预算约束） | ≤ 2s |
| uvicorn 1012 之后等待连接任务 | 20s |
| lifespan 兜底 | 2s + 2s |
| **合计（不含进程销毁）** | **≈ 36s** |

systemd 默认 `TimeoutStopSec=90s`，留有余量。

### 2.5 设备侧：取消可重试 + 逐台广播

```python
try:
    async with device_lifecycle_lock(session.device_id):
        ...
        reason = session.close_reason or "server_shutdown"
except BaseException:
    # 被上层总时限取消：放回集合，lifespan 兜底还能再试
    self._sessions.add(session)
    raise
if was_current:
    await self._publish_status(session.device_id, online=False, reason=reason)
```

两处变化：

1. 取消时把正在处理的会话放回 `_sessions`，不再凭空丢失（旧实现 pop 之后被取消，
   该链路既没 close 也没广播）。
2. offline 从“全部关闭后统一广播”改为“每台在锁外立即广播”。慢 App 仍不会拉长
   生命周期锁；取消时最多丢失“正在广播的那一条”，已关闭的设备不会被后面的慢设备
   连累。

### 2.6 App 侧：`shutdown(timeout)` 的剩余预算

每个 `AppSession.close()` 用 `asyncio.wait_for(..., remaining)`；预算耗尽/超时就清空
集合、取消订阅，剩余连接交给 uvicorn 1012。单测用一个 `close()` 永久挂起的 channel
证明 0.1s 预算能生效。

### 2.7 `run()` 与 uvicorn 对齐（M-2）

- 补 `finally: if config.uds and os.path.exists(config.uds): os.remove(config.uds)`；
- `reload=True` 且 `config.app` 不是导入字符串时显式 `SystemExit`（否则
  `should_reload=False`，会静默单进程运行，正是上一轮修掉的那类降级）；
- `workers>1` 仍显式拒绝。

未补 `config.load_app()` / `startup` 失败后的 `sys.exit(3)`：uvicorn 0.54 的
`Server.startup()` 自己会 `sys.exit(3)`（上一轮已实测），`_serve()` 内的
`config.load()` 也会加载应用并保持同样的失败语义。入口只支持 `python -m` 的字符串
app + TCP，已在 docstring/README 写明。

### 2.8 测试与 mutation 可复核（M-3/M-5/M-6）

- `test_lifespan_resets_shutdown_flags` 在 `finally` 里 `open_for_connections()` 复位
  两个网关，不再把模块级状态留给后续用例。
- 新增 `companion/tests/test_phase3_app_gate_e2e.py`：真实 uvicorn/TCP 下，置位
  `_shutting_down` 后新连 `/app/ws`，断言 `(1011, "server_shutdown")`。
- mutation 脚本纳入仓库：`companion/tests/mutation/round15_shutdown.py`。每个变体
  原地改写、`finally` 还原并断言内容与原文一致；字符串不匹配会直接失败（防止默默
  改错位置）。

## 3. 改动文件清单

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/services/application_shutdown.py` | **新增**：停机顺序、四项预算、`bounded_shutdown_application()` |
| `companion/server/app/server.py` | 截止时间 + `force_exit` 轮询、异常/超时区分、`run()` 的 uds 清理与非字符串 app 拒绝 |
| `companion/server/app/main.py` | lifespan 改用有界兜底；`build_config()` 设置 `timeout_graceful_shutdown=20` |
| `companion/server/app/services/app_gateway.py` | `shutdown(timeout)`：每个 close 受剩余预算约束，超时清空交 1012 |
| `companion/server/app/services/device_gateway.py` | 取消时会话放回集合；offline 逐台在锁外广播 |
| `companion/server/tests/test_server.py` | 中窗 `force_exit`、lifespan 有界、App 关闭预算、`build_config`、对象 app 拒绝；复位标志（12 例） |
| `companion/server/tests/test_app_gateway.py` | 挂起的 App `close()` 受预算约束（24 例） |
| `companion/server/tests/test_device_gateway.py` | 取消后会话保留可重试（46 例） |
| `companion/tests/test_phase3_app_gate_e2e.py` | **新增**：真实 TCP 下停机窗口新 App 连接 1011 |
| `companion/tests/mutation/round15_shutdown.py` | **新增**：11 个可复现 mutation |
| `companion/docs/protocol_v2.md` | §8.2 补停机预算与超时语义 |
| `companion/README.md` | 启动章节补预算/单进程说明 |
| `docs/语音机器人需求文档/v2_migration_log/20_phase3_shutdown_hardening.md` | 追加 §10 勘误 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 21 |

## 4. 新增/强化用例

| 用例 | 锁住什么 |
| :--- | :--- |
| `test_force_exit_during_application_shutdown_abandons_wait` | 窗口内第二次 Ctrl+C ≤ 1s 生效（M-1） |
| `test_lifespan_shutdown_is_bounded` | 第一次超时后兜底 2s 内收尾（I-1） |
| `test_application_shutdown_bounds_app_close` | `finally` 关 App 必须带独立预算（I-1） |
| `test_application_shutdown_timeout_still_runs_uvicorn_shutdown` | 截止时间生效且错误地与常量无关也红 |
| `test_build_config_sets_graceful_shutdown_timeout` | 1012 之后的 drain 有界（I-1） |
| `test_run_rejects_reload_with_object_app` | 非字符串 app + reload 不得静默单进程（M-2） |
| `test_shutdown_cancelled_midway_keeps_session_for_retry` | 取消后会话放回集合（I-1） |
| `test_shutdown_deadline_does_not_wait_for_hanging_app_close` | App close 卡住 ≤ 1s 收尾（I-1） |
| `test_new_app_connection_after_shutdown_started_gets_1011` | 真实 TCP 下 1011（M-5） |

## 5. Mutation（`companion/tests/mutation/round15_shutdown.py`）

```text
RED  app close in finally has no budget
RED  lifespan helper ignores its timeout
RED  force_exit is not polled mid-window
RED  application deadline ignores the configured value
RED  reload branch removed
RED  object app accepted with reload
RED  graceful drain timeout not configured
RED  lifespan does not reset app gateway flag
RED  cancelled device session is not put back
RED  app gateway deadline ignores its timeout
RED  app gateway accepts connections during shutdown
MUTATIONS THAT STAYED GREEN: none
```

脚本在还原后逐文件断言 `read_text() == original`，所有用例都通过。

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
228 passed in 19.92s
228 passed in 19.81s

$ & "...\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py \
    companion/tests/test_phase3_shutdown_e2e.py companion/tests/test_phase3_app_gate_e2e.py
18 passed in 13.51s
18 passed in 13.82s

$ & "...\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py \
    companion/server/tests/test_app_gateway.py companion/server/tests/test_server.py
82 passed in 2.91s      # 46 + 24 + 12

$ & "...\python.exe" companion/tests/mutation/round15_shutdown.py
MUTATIONS THAT STAYED GREEN: none
```

旧/新探针输出见 §1.1 / §1.2。

## 7. Ruling 与接受的风险

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 总预算 10s + 2s + 20s + 2s，而不是“一次 shutdown 里无限等待” | systemd/docker 的停止宽限更短；无界等待会被 SIGKILL，反而丢事件 | 极慢 DB 下最后一次 offline 可能来不及发（进程本来也活不下来） |
| lifespan 仍重试一次设备关闭，但只给 2s | 第一次可能因单纯超时而被截断，重试能救回少量设备；2s 保证不会把进程再拖满一轮 | 多花 ≤ 4s（含 App 关闭） |
| 取消时把会话放回集合 | 否则该链路既没 close 也没广播，凭空消失 | 兜底可能重复处理（`close()` 幂等） |
| offline 逐台广播（锁外） | 取消时最多丢一条，而不是全部；锁持有时间不受慢 App 影响 | offline 与其它设备事件可能交错（语义无害） |
| `AppSession.close` 受总预算约束 | 一个卡住的 App 不能拖住整个停机 | 预算耗尽时部分 App 收到 1012 而非 1011 |
| `force_exit` 用 0.1s 轮询 | 不必等满 10s；与 uvicorn 的 `force_exit` 语义一致 | 轮询带来 ≤0.1s 的额外延迟 |
| 非字符串 app + reload 显式失败 | 与 `uvicorn.run()` 一致，避免静默单进程 | 想用工厂函数时必须改回字符串入口 |

### 接受的风险（不修）

- Windows 上 uvicorn reload 的**重启**（`os.kill(pid, CTRL_C_EVENT)`）在本机不可用；
  旧入口同样如此，属于 uvicorn 自身，不是本提交的回归。
- 多 worker / 多进程：显式拒绝，未实现跨进程停机协调。
- 超时后剩余连接由 uvicorn 1012 收尾，最多丢失最后一次 `device.status offline`
  广播（`reason=server_shutdown`）。
- `timeout_graceful_shutdown=20s` 只配置在 `python -m` 入口的 `build_config()`；
  直接 `uvicorn ...:app` 仍不可达该保证（协议/README 已写明）。
- Android App 对 1011/1012 的处理未验证（App 未实现）。
- 既有保留项：`device.report` 落库后广播前窗口、模拟器未实现的设备侧限流/看门狗/
  角度截断、e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408 等。

## 8. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 ack 前控制帧的容忍度仍需硬件验收。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `cd358a2f..HEAD`（重点：预算数学、取消语义、
   `force_exit` 轮询、`timeout_graceful_shutdown` 与 lifespan 的交互、新 e2e 的
   隔离性、mutation 脚本的还原）。
3. 审查通过后进入音频 Block。

## 10. 勘误（日志 22 修订）

- **§2.5 的「被上层总时限取消时对当前会话 `self._sessions.add(session)`，兜底还能
  再试一次」只对“关闭”成立，对“广播”不成立。** 取消若落在
  `unregister_device_link()` 之后（例如 `channel.close()` 卡住），重试时
  `was_current` 已为 False、注册表也没有链路，offline 会被永久跳过。日志 22 改为
  在会话上记录 `pending_offline_reason`，并让 `_teardown()` 的取消路径共用同一机制。
  真实探针：修复前 `after retry: offline_events=[]`，修复后
  `after retry: offline_events=['server_shutdown']`。
- **§2.5 / §7 的「最多丢失正在广播的那一条」需要补充限定**：这里指同一次
  `shutdown()` 调用中已经被取消的那一条；跨重试不丢（at-least-once），但广播若在
  总线已投递后才被取消，接收方可能看到重复的 `online=false`。
- **§7 的「兜底可能重复处理（`close()` 幂等）」低估了广播的重复风险。** 首次实现
  “广播后再清空待发原因”会让并发的 `_teardown()` 重复发一次 offline，真实 e2e
  变成 `[True, False, False]`；日志 22 改为 `await` 前清空、取消时写回，并加了
  并发用例。
- **§4 的 `test_lifespan_shutdown_is_bounded` 会把 `ws.app_gateway` 留在停机态**
  （审查 M-1）：新增用例已补 `try/finally` 复位；该用例同时加了 3s `wait_for`，
  避免兜底被改成无界时挂住整个测试会话（审查 M-2）。
- **§7 的「超时后……最多丢失最后一次设备下线广播」（以及 §2.5 的「正在广播的那
  一条」）仅指预算耗尽、连接被 uvicorn 以 1012 强制收尾这一路径**；进入应用层重试
  的 offline 已改为 at-least-once（可能重复，不会丢），两者不矛盾，但并排阅读时
  需要区分场景。
