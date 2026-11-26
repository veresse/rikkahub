# 20 · Phase 3 停机收尾：reload、总时限与 App 连接门控

- 日期：2026-11-26
- 状态：✅ 完成（全量 220 passed；真实 uvicorn 停机 e2e 仍通过）
- 分支：`feature/companion-robot-v2`
- 审查范围：`a6f221ab..e9fde99e`（日志 19 的提交）
- 未涉及：音频链路、Android App、ESP32 固件

## 1. 审查结论

独立审查对 `e9fde99e` 给出 **0 Critical / 2 Important / 4 Minor**，结论为
“With fixes”。核心修复（`CompanionServer` 在 uvicorn 关连接前下线）经真实
uvicorn e2e 与探针确认成立；Important 都在停机**之外**的工程性问题上：

| ID | 内容 |
| :--- | :--- |
| I-1 | `CompanionServer(config).run()` 静默忽略 `reload=True`：日志仍打印 “Will watch for changes”，实际不会 reload（`uvicorn.run()` 里的 `ChangeReload` 分支没有被执行） |
| I-2 | 应用层停机没有总时限，`force_exit` 也会等；日志 §7 的「writer 冲刷 ≤2s」不覆盖取消后 `_flush_reports()` 的 DB 写（`busy_timeout=15000`） |
| M-1 | `test_shutdown_reports_reason_of_already_closed_link` 的断言在 `task.cancel()` 之后，`_teardown()` 可能替 `shutdown()` 补发同一条 offline；「已 closed 就 `continue`」的变体仍 GREEN |
| M-2 | 停机窗口内新连入的 `/app/ws` 被 `AppGateway.shutdown()` 的 `clear()` 丢弃，随后由 uvicorn 以 1012 收尾，不是协议承诺的 1011 |
| M-3 | `_teardown()` 仍在生命周期锁内广播 offline（相邻残留，非本轮引入） |
| M-4 | 「该保证只在 `python -m` 入口成立」没有写进协议/部署文档 |

## 2. 修复与决策

### 2.1 I-1：`run(config)` 照抄 `uvicorn.run()` 的 reload 分支

```python
def run(config: uvicorn.Config) -> None:
    if config.workers > 1:
        raise SystemExit(
            "workers > 1 is not supported: graceful device shutdown requires "
            "CompanionServer in a single process"
        )
    server = CompanionServer(config)
    try:
        if config.should_reload:
            sock = config.bind_socket()
            ChangeReload(config, target=server.run, sockets=[sock]).run()
        else:
            server.run()
    except KeyboardInterrupt:
        pass
```

`main.py` 的 `__main__` 改为 `run(config)`。

**真实探针**（同一台 Windows 主机，`DEBUG=true`，健康检查轮询）：

```text
# 修复前（CompanionServer(config).run()）
health before reload: 200
reload marker: False
---- markers ----
INFO:     Started server process [30920]
INFO:     Application startup complete.

# 修复后（python -m companion.server.app.main）
startup marker: Started reloader process
INFO:     Started reloader process [25212] using WatchFiles
INFO:     Started server process [19708]
INFO:     Application startup complete.
WARNING:  WatchFiles detected changes in 'companion\server\app\services\clock.py'. Reloading...
```

**未验证项（如实记录）**：在本机（Windows、uvicorn 0.54.0），检测到变更后
`BaseReload.restart()` 卡在 `os.kill(pid, signal.CTRL_C_EVENT)` → `process.join()`，
新 child 没有起来；用**旧的** `uvicorn.run(reload=True)` 入口做同样探针，行为完全
相同（同样停在 `Reloading...`，健康检查不中断）。这是 uvicorn 自己的 Windows 重启
路径，不由本提交引入，本提交只保证「分支选择 + 变更检测」恢复。reloader 的
`ChangeReload(config, target=server.run, ...)` 与 uvicorn 原生调用完全一致。

多 worker：`Multiprocess` 的每个 worker 都是普通 `uvicorn.Server`，不会执行本模块的
停机顺序。项目按单进程部署，`run()` 对 `workers>1` 直接 `SystemExit`，避免静默
降级成「App 收不到 `server_shutdown`」。

### 2.2 I-2：应用层停机总时限 + `force_exit`

```python
APP_SHUTDOWN_TIMEOUT_SECONDS = 10.0

async def shutdown(self, sockets=None) -> None:
    try:
        if self.force_exit:
            logger.warning("force exit: skipping application shutdown")
        else:
            try:
                await asyncio.wait_for(
                    self._shutdown_application(), APP_SHUTDOWN_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "application shutdown exceeded %.1fs, "
                    "falling back to uvicorn connection cleanup",
                    APP_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except Exception:
                logger.exception("application shutdown failed")
    finally:
        await super().shutdown(sockets)
```

- 10s 是**软上界**：超时取消后，`_shutdown_application()` 的 `finally`（关闭 App
  通道）仍会跑完，`wait_for` 要等任务真正结束才抛 `TimeoutError`；因此实际耗时是
  「10s + 关闭 App 通道」，不会无限增长。
- 超时后交给 uvicorn 的 1012 收尾：进程即将退出，继续等只会让 systemd/docker 的
  停止宽限先到并 SIGKILL，连已经发出的 offline 也可能丢。
- `force_exit`（第二次 Ctrl+C）直接跳过应用层关闭。`super().shutdown()` 仍在
  `finally` 里，保证连接清理与 lifespan 收尾一定会跑。
- 日志 19 §7 的「≤2s」已勘误：`stop_writer()` 的两个 1s 等待之外，取消后的
  `_flush_reports()` 还要写 SQLite（`busy_timeout=15000`），单设备就可能到秒级。

### 2.3 M-1：把断言移到 `task.cancel()` 之前

```python
try:
    await harness.gateway.shutdown()

    # 断言必须在取消 `handle()` 之前：取消会触发 `_teardown()`，它自身可能
    # 补发这条 offline，从而掩盖 `shutdown()` 漏发的情况。
    offline = [...]
    assert [e.get("reason") for e in offline] == ["device_revoked"], harness.events
    assert get_device_link(DEVICE_ID) is None
finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
```

新增 mutation「`shutdown()` 遇到已 closed 的链路整体 `continue`」现在为 **RED**。

### 2.4 M-2：`AppGateway` 对称的 `_shutting_down`

```python
async def handle(self, token_id, channel) -> None:
    self._prune_revoked()
    if self._shutting_down:
        try:
            await channel.close(1011, "server_shutdown")
        except Exception:
            logger.debug("app close failed during shutdown: %s", token_id, exc_info=True)
        return
    session = AppSession(token_id, channel, self.bus)
    self._sessions.setdefault(token_id, set()).add(session)
    ...

async def shutdown(self) -> None:
    self._shutting_down = True
    for sessions in list(self._sessions.values()):
        for session in list(sessions):
            await session.close(1011, "server_shutdown")
    self._sessions.clear()
    ...
```

检查与注册之间没有 `await`，所以不存在「检查通过、停机置标志、之后才注册」的窗口：
同一事件循环里要么注册先完成（快照能取到），要么停机标志先置位（新连接直接被 1011
拒绝）。`lifespan` 启动时调用 `app_gateway.open_for_connections()` 复位，避免同进程
重启后所有 App 都连不上（设备网关早就有同名方法）。

### 2.5 M-4：文档写明入口要求

- `companion/docs/protocol_v2.md` §8.2：停机时离线事件/1011 的语义，以及
  「要求 `python -m companion.server.app.main`；直接 `uvicorn ...:app` 只会收到 1012」。
- `companion/README.md` 启动章节：同样的提示 + `workers>1` 不受支持。

### 2.6 M-3：**不修**（保留 `_teardown()` 锁内广播）

把 `_teardown()` 的 `_publish_status()` 移出生命周期锁会破坏事件顺序：新链路可以
在锁外先完成注册并广播 `online=True`，然后旧链路的 `offline` 才发出，App 最终停在
`online=false` 但设备其实在线。锁内广播保证「offline(旧) → online(新)」的单调顺序。
代价是同设备的撤销/重连最多等一次 App 发送超时（`SEND_TIMEOUT_SECONDS=2`），这是
有界等待，不是死锁。停机路径可以移出锁，是因为它在 `shutdown()` 里先注销了链路，
重连会看到 `_shutting_down` 而被拒绝。

## 3. 改动文件清单

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/server.py` | `APP_SHUTDOWN_TIMEOUT_SECONDS`、`force_exit` 跳过、`_shutdown_application()`（可被单测覆盖）、`run(config)`（reload 分支 + 多 worker 拒绝） |
| `companion/server/app/main.py` | `__main__` 改用 `run(config)`；lifespan 启动时复位 `app_gateway` 的停机标志 |
| `companion/server/app/services/app_gateway.py` | `_shutting_down` / `open_for_connections()`；停机后新连接直接 `1011`；`shutdown()` 先置标志 |
| `companion/server/tests/test_server.py` | **新增**：reload 分支、多 worker 拒绝、超时/异常/`force_exit` 的收尾、lifespan 复位（7 例） |
| `companion/server/tests/test_app_gateway.py` | 新增停机后新连接 1011、`open_for_connections()` 复位（2 例） |
| `companion/server/tests/test_device_gateway.py` | `test_shutdown_reports_reason_of_already_closed_link` 断言移到取消之前 |
| `companion/docs/protocol_v2.md` | §8.2 停机语义 + 入口要求 |
| `companion/README.md` | 启动入口 / 单进程说明 |
| `docs/语音机器人需求文档/v2_migration_log/19_phase3_shutdown_chain.md` | 追加 §10 勘误 |
| `docs/语音机器人需求文档/v2_migration_log/20_phase3_shutdown_hardening.md` | 本日志 |
| `docs/语音机器人需求文档/v2_migration_log/README.md` | 索引新增 20 |

## 4. 测试

| 用例 | 锁住什么 |
| :--- | :--- |
| `test_run_uses_change_reload_when_config_asks` | `reload=True` 必须走 `ChangeReload`（I-1 回归） |
| `test_run_starts_companion_server_without_reload` | 非 reload 仍走 `CompanionServer.run()` |
| `test_run_rejects_multiple_workers` | `workers>1` 显式 `SystemExit` |
| `test_application_shutdown_timeout_still_runs_uvicorn_shutdown` | 超时后 uvicorn 清理必须执行，且总耗时 < 2s（I-2） |
| `test_application_shutdown_error_still_runs_uvicorn_shutdown` | 应用层抛错不能吞掉清理、也不能把异常抛给 uvicorn |
| `test_force_exit_skips_application_shutdown` | 第二次 Ctrl+C 不再等应用层关闭 |
| `test_lifespan_resets_shutdown_flags` | 同进程重启后两个网关都重新接受连接 |
| `test_new_connection_during_shutdown_is_closed_with_1011` | 停机后新 App 连接 1011 且不登记（M-2） |
| `test_open_for_connections_allows_new_connections_after_shutdown` | 复位后可正常连接 |
| `test_shutdown_reports_reason_of_already_closed_link`（强化） | 断言在取消之前，`shutdown()` 必须自己发出这条 offline（M-1） |

## 5. Mutation（原地改写，`finally` 还原；工作区最终干净）

```text
RED  ready set before _mark_ready
RED  is_active branch removed
RED  post-await closed check removed
RED  offline reason dropped
RED  revoke commit outside lifecycle lock
RED  shutdown offline event dropped
RED  shutdown skips already-closed link (narrow)
RED  shutdown skips already-closed link (continue)      # 日志 19 的盲区，本轮才红
RED  shutdown always reports server_shutdown
RED  install accepted after shutdown started
RED  mark_ready publishes online after shutdown started
RED  app shutdown still accepts new connections
RED  app shutdown never sets the flag
RED  run() ignores should_reload
RED  force_exit branch ignored
RED  no total deadline for application shutdown         # 30.68s 后按 elapsed 断言失败
RED  application shutdown exceptions propagate
RED  lifespan does not reset app gateway flag
RED  lifespan does not reset device gateway flag
RED  CompanionServer runs app shutdown after uvicorn    # 真实 uvicorn e2e
MUTATIONS THAT STAYED GREEN: none
```

## 6. 实际执行的命令与真实输出

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion
220 passed in 18.62s
220 passed in 18.62s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/tests/test_phase3_e2e.py companion/tests/test_phase3_shutdown_e2e.py
17 passed in 13.16s

$ & "...\companion-venv\Scripts\python.exe" -m pytest -q companion/server/tests/test_device_gateway.py companion/server/tests/test_app_gateway.py companion/server/tests/test_server.py
75 passed in 2.20s      # 45 + 23 + 7

$ & "...\companion-venv\Scripts\python.exe" C:\...\round14_mutation_check.py
MUTATIONS THAT STAYED GREEN: none
```

真实 reload 探针（见 §2.1）与真实 uvicorn 停机 e2e 见 §2.1 / 日志 19。

## 7. Ruling 与接受的风险

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 应用层停机 10s 总时限 + 超时后交给 uvicorn | systemd/docker 的停止宽限通常更短；无界等待会被 SIGKILL，反而丢事件 | 极慢的 DB 下最后一次 offline 可能来不及发（进程本来也活不下来） |
| `force_exit` 直接跳过应用层关闭 | 运维第二次 Ctrl+C 的意图就是立刻停 | 该次停机不保证 offline，但连接仍被 uvicorn 1012 清理 |
| `workers>1` 直接拒绝 | 每个 worker 是普通 `Server`，保证不成立；静默降级更难排查 | 想横向扩展时必须先实现跨进程停机协调 |
| App 侧用 `_shutting_down`（而不是关监听 socket） | 与设备侧对称、不触碰 uvicorn 内部；检查与注册之间没有 await | 停机窗口内新 App 会先完成 HTTP 鉴权再拿到 1011（可接受） |
| `_teardown()` 的 offline 仍留在锁内（M-3 不修） | 保证「offline(旧) → online(新)」的顺序；移出锁会让 App 停在错误的 online=false | 同设备撤销/重连最多多等 2s |
| 测试用 `sleep(30)` + `elapsed < 2s` 锁总时限 | 直接断言「不会被等待」；去掉 `wait_for` 时用例在 30s 后失败而不是永久挂起 | 该 mutation 让测试慢 30s |

### 接受的风险（不修）

- Windows 上 uvicorn reload 的**重启**（`os.kill(pid, CTRL_C_EVENT)`）在本机不可用；
  旧入口同样如此，属于 uvicorn 自身，不是本提交的回归。
- 多 worker / 多进程：显式拒绝，未实现跨进程停机协调。
- 10s 超时后取消不保证 `_flush_reports()` 的 DB 写入完成（进程即将退出）。
- App 端对 `server_shutdown` / 1011 / 1012 的处理仍未验证（Android App 未实现）。
- `device.report` 落库后广播前窗口、模拟器未实现的设备侧限流/看门狗/角度截断、
  e2e 未覆盖 `servo.move`/`idle.set`/校准/4409/4408 等既有项保持原状。

## 8. 未完成项 / 阻塞点

- 音频 Block（ASR/TTS/barge-in）未开始；音频验收必须真机。
- Android App 未开始。
- 真实 ESP32 固件对 ack 前控制帧的容忍度仍需硬件验收。

## 9. 下一步

1. 提交本轮修复并推送。
2. 派独立子智能体审查 `e9fde99e..HEAD`（重点：`run()` 的 reload 分支与多 worker 拒绝、
   10s 软上界的实际行为、`AppGateway` 门控的竞态、M-1 强化后的测试是否仍能自证）。
3. 审查通过后进入音频 Block。

## 10. 勘误（日志 21 修订）

- **§2.2 的「10s 是软上界……实际耗时是 10s + 关闭 App 通道，不会无限增长」不成立。**
  `wait_for` 只约束第一次应用层关闭；lifespan 兜底会**再无条件跑一遍无界的**
  `device_gateway.shutdown()`，`AppSession.close()` 没有超时，1012 之后的连接 drain
  也是 `timeout_graceful_shutdown=None`。日志 21 加入 10s/2s/2s/20s 四层预算，并用
  真实 uvicorn 探针复现：旧提交慢设备 30s 时停机 30.41s，新提交 2.39s。
- **§2.2 / §7 的「`force_exit` 直接跳过应用层关闭」只在停机开始前成立。** 第二次
  Ctrl+C 落在应用层关闭窗口内时，旧实现仍会等满时限；日志 21 改为 0.1s 轮询，探针从
  4.61s 降到 0.16s。
- **§5 的「no total deadline for application shutdown → RED」只证明了第一次
  `wait_for` 生效，不能证明“总”时限。** 真正的无界来自 lifespan 重跑与 App close；
  日志 21 的 mutation 把这三点分别锁死，并把脚本纳入
  `companion/tests/mutation/round15_shutdown.py`。
- **§2.4 的 App 门控论证不受影响**：`_prune_revoked()` / `AppSession(...)` /
  `setdefault().add()` 之间确实没有 `await`，检查与注册不存在窗口。
- **§4 新增的 `test_lifespan_resets_shutdown_flags` 会把模块级网关留在
  `_shutting_down=True`**，属于测试隔离隐患；日志 21 已在 `finally` 中复位。
- **§2.1 的 `run()` 与 `uvicorn.run()` 仍有两处未对齐**：uds 未清理、非字符串 app +
  reload 未显式拒绝；日志 21 已补齐（`Server.startup()` 的 exit 3 语义经实测一致）。
