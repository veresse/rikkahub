# 25 · 音频 Block B：Mock 管线 / 打断 / 落库 / 网关接线

- 状态：**已完成（Task 1–8，已按独立审查修复）**
- 日期：2026-11-26
- 分支：`feature/companion-robot-v2`
- 设计依据：`23_audio_block_design.md`（§6 生命周期、§7 provider、§8 会话/取消、§9 barge-in、§10 节流、§11 超时、§14 子块 B）
- 实施计划：`docs/superpowers/plans/2026-11-26-companion-audio-block-b.md`

## 1. 目标与范围

Block B 把 Block A 的传输骨架接成「能离线跑完整一轮」的语音管线：

- 流式分句（LLM 文本 → 可朗读句段）；
- Mock ASR/TTS provider（不依赖云 Key）；
- 下行节流发送器（文本段 + 音频帧、`turn_seq` 递增、`last` 标记、背压）；
- `VoiceSession` 完整轮次：收听 → 识别 → 生成 → 朗读 → 播放回执；
- barge-in 取消：本地停播 + 代次递增 + `cancel_ack` + `action.stop(barge_in)`；
- 取消/断线时把已生成内容按 `interrupted` 落库；
- 设备 → 会话绑定与语音运行时装配、网关注入、关闭清理。

不在本块：DashScope 真实 ASR/TTS（Block C）、Android App 对齐、真机验收。

## 2. 改动文件（Task 1–8）

### 新增

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/audio/sentences.py` | `SentenceSegmenter`：增量喂文本，按中英文标点/换行切句，超长硬切 |
| `companion/server/app/audio/providers.py` | `ProviderError`、ASR/TTS 协议、`MockAsrProvider/MockAsrStream/MockTtsProvider`、`Unavailable*` |
| `companion/server/app/audio/downlink.py` | `DownlinkSender`：`SegmentStart` + `AudioPacket`、节流、`turn_seq`、缓冲背压 |
| `companion/server/app/audio/runtime.py` | `VoiceConfig` / `VoiceRuntime`（bus、provider、会话解析、聊天工厂） |
| `companion/server/app/audio/binding.py` | `resolve_conversation_for_device()`、`build_voice_runtime()` |
| `companion/server/tests/test_audio_sentences.py` | 17 例分句测试 |
| `companion/server/tests/test_audio_providers.py` | 10 例 Mock/Unavailable provider 测试 |
| `companion/server/tests/test_audio_downlink.py` | 9 例下行发送器测试 |
| `companion/server/tests/test_audio_pipeline.py` | 16 例完整管线测试（正常/打断/超时/provider 失败/end-before-begin） |
| `companion/server/tests/test_audio_binding.py` | 10 例绑定与运行时装配测试 |
| `companion/server/tests/test_chat_cancel.py` | 取消落库与 `cancel_reason` 透传测试 |
| `companion/tests/mutation/audio_block_b.py` | 14 个 Block B 变异用例（分句/下行/管线/落库/绑定/网关 ack） |

### 修改

| 文件 | 说明 |
| :--- | :--- |
| `companion/server/app/audio/pipeline.py` | `VoiceSession` 增加 runtime 模式：`begin/end_user_speech/cancel_turn/segment_played/shutdown`、计时器、事件发布 |
| `companion/server/app/services/orchestrator.py` | `stream_chat(cancel_reason=...)`、`_save_turn()`、`_persist_cancelled()`；取消时保留部分回复并标 `interrupted` |
| `companion/server/app/services/action_orchestrator.py` | `ActionTurnState.cancel_reason`；取消路径透传 `barge_in`（不再固定 `server_cancel`） |
| `companion/server/app/services/device_gateway.py` | `voice_runtime` 注入、`voice.start` 后台 `begin()`、`voice.cancel` → `cancel_turn(reason)`、`close_voice()` → `shutdown()` |
| `companion/server/app/routers/ws.py` | 模块初始化 `build_voice_runtime()` 并注入 `device_gateway` |
| `companion/server/app/config.py` | `VOICE_ENABLED/VOICE_PROVIDER/…` 九项超时与节流配置、`DASHSCOPE_TTS_CONTAINER` |
| `companion/server/tests/test_voice_transport.py` | runtime 接入、unbound/asr_unavailable、cancel 代次、断线清理用例 |
| `companion/tests/test_phase3_e2e.py` | Block A 两条纯传输用例显式关闭 runtime；新增 `install_mock_voice` 与 5 例真实 TCP/WS 语音 e2e |
| `companion/simulators/device_simulator.py` | `voice.segment` 自动回执（新段回执上一段、末帧回执当前段、取消清空、关闭时取消 ack 任务） |
| `companion/docs/protocol_v2.md` | `voice.event` 来源与字段、末段回执结束轮次、`playback_timeout`、取消只停一次 |
| `companion/tests/test_protocol_v2_doc.py` | Block B 协议契约断言 |

## 3. 关键实现

### 3.1 分句

- 只在标点后切分，避免把「3.14」「Mr.」类文本切碎；连续标点合并；未闭合引号/括号内暂不切。
- `flush()` 返回剩余文本；单段超过 `max_chars` 时硬切，保证 TTS 首包延迟有上界。

### 3.2 Mock provider

- ASR 不解析 Opus：统计上行 Ogg 页数，按脚本在第 N 页发 partial、第 M 页发 final；
  可注入延迟/错误/超时，`close()` 后事件流结束。
- TTS 用 `OggOpusMuxer` 把合成 payload 包成 Ogg，按可配置块大小/延迟产出；
  无音频产物时视为 `tts_unavailable`。

### 3.3 下行与背压

- 每段先发 `voice.segment` JSON，再发该段音频帧；两者在同一把设备发送锁内完成，避免 App/控制帧插入。
- `turn_seq` 每轮自增；`FLAG_LAST` 只打在每轮最后一帧；发送按帧长节流，
  缓冲超过 `VOICE_DOWNLINK_BUFFER_SECONDS` 时等待，不无限堆积。

### 3.4 完整轮次

- `voice.start` 只做同步 `start()`；网关 `create_task(begin())`，不阻塞读循环。若 `voice.end` 在 `begin()` 打开 ASR 前到达，轮次挂起，等 ASR 就绪后补开，避免误报 `asr_unavailable`。
- `begin()` 解析会话 → 检查 App 对话锁 → 打开 ASR → 启动上行泵/ASR 读循环/空闲与总时长看门狗。
- 只有 `segment_played(last)` 才结束轮次；越界 `segment_seq` 忽略；迟到消息按 `last_turn_id` 幂等。
- 超时映射：空闲 → `idle_timeout`；总时长/播放回执 → `internal_error`；ASR/TTS → `*_unavailable`；
  取消/断线 → `turn_end(timeout|interrupted|error)`。

### 3.5 打断

- `cancel_turn(reason)` 同步置 IDLE、递增代次、停播、取消任务，后台有界清理；旧代次的 TTS/上行/计时器一律丢弃。
- 网关回 `cancel_ack`（迟到重放同轮 `cancel_ack`，幂等例外见协议 §5.4）。
- 被取消的聊天生成器发出一次 `action.stop(reason="barge_in")`；重复取消不会重复下发。

### 3.6 落库

- 正常结束：保存用户文本 + 助手完整回复。
- 取消/断线：保存用户文本；助手消息带已生成的部分内容并标 `interrupted=true`；
  空回复不落空消息（错误原因由事件表达）。
- 取消原因从设备 `voice.cancel.reason` 透传到动作编排与落库。

### 3.7 会话绑定

解析顺序（`resolve_conversation_for_device`）：

1. 设备 `bound_conversation_id` 且会话仍存在；
2. `bound_assistant_id` 对应角色最近更新的会话；
3. 都没有 → 新建「与 <设备名> 的语音对话」；
4. 设备已撤销 / 无任何绑定 → `None`，管线回 `unbound`（不自动建会话）。

已删除的会话 id 会回退到角色最近会话，而不是直接 `unbound`。

### 3.8 运行时装配

- `VOICE_ENABLED=False` → `build_voice_runtime()` 返回 `None`，网关保持 Block A 同步门控语义。
- `VOICE_PROVIDER=mock` → Mock ASR/TTS；`unavailable` → 明确 `asr_unavailable`；`dashscope` 等未知值在 Block C 前一律降级为不可用并 warning。
- `chat_factory` 在生成器存活期间保持同一个 DB session，取消落库与正常落库共用。

## 4. 实际命令与结果（Task 1–8）

测试解释器（venv 无 pip）：

```powershell
$py="C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv\Scripts\python.exe"
```

| 阶段 | 命令 | 结果 |
| :--- | :--- | :--- |
| Task 1 | `& $py -m pytest companion/server/tests/test_audio_sentences.py -q` | 17 passed |
| Task 2 | `& $py -m pytest companion/server/tests/test_audio_providers.py -q` | 10 passed |
| Task 3 | `& $py -m pytest companion/server/tests/test_audio_downlink.py -q` | 9 passed |
| Task 4 | `& $py -m pytest -q` | 353 passed |
| Task 5 | `& $py -m pytest -q` | 359 passed |
| Task 6 定向 | `& $py -m pytest companion/server/tests/test_audio_binding.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py companion/server/tests/test_ws_router.py -q` | 100 passed |
| Task 6 全量 | `& $py -m pytest -q` | **377 passed** |
| Task 7 e2e | `& $py -m pytest companion/tests/test_phase3_e2e.py -q` | 23 passed（含 5 例语音 e2e） |
| Task 7 协议 | `& $py -m pytest companion/tests/test_protocol_v2_doc.py -q` | 10 passed |
| Task 7 管线 | `& $py -m pytest companion/server/tests/test_audio_pipeline.py -q` | 16 passed（新增 end-before-begin 用例） |
| Task 8 定向 | `& $py -m pytest companion/server/tests/test_audio_sentences.py companion/server/tests/test_audio_providers.py companion/server/tests/test_audio_downlink.py companion/server/tests/test_audio_pipeline.py companion/server/tests/test_audio_binding.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_voice_transport.py companion/server/tests/test_device_gateway.py -q` | 150 passed |
| Task 8 全量 | `& $py -m pytest -q` | **386 passed / 0 failed** |
| Task 8 变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 审查修复定向 | `& $py -m pytest companion/server/tests/test_audio_pipeline.py companion/server/tests/test_audio_downlink.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_voice_transport.py -q` | 67 passed |
| 审查修复全量 | `& $py -m pytest -q` | **392 passed / 0 failed** |
| 审查修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 复审修复定向 | `& $py -m pytest companion/server/tests/test_audio_pipeline.py companion/server/tests/test_audio_downlink.py companion/server/tests/test_chat_cancel.py companion/server/tests/test_voice_transport.py -q` | 72 passed |
| 复审修复全量 | `& $py -m pytest -q` | **397 passed / 0 failed** |
| 复审修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 三轮修复定向 | `& $py -m pytest companion/server/tests/test_chat_cancel.py -q` | 8 passed |
| 三轮修复全量 | `& $py -m pytest -q` | **399 passed / 0 failed** |
| 三轮修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 四轮修复定向 | `& $py -m pytest companion/server/tests/test_chat_cancel.py -q` | 9 passed |
| 四轮修复全量 | `& $py -m pytest -q` | **400 passed / 0 failed** |
| 四轮修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 五轮修复定向 | `& $py -m pytest companion/tests/test_phase3_e2e.py::test_voice_start_right_after_barge_in_is_not_busy -q` | 1 passed（单跑连跑 6 次稳定） |
| 五轮修复全量 | `& $py -m pytest -q` | **401 passed / 0 failed** |
| 五轮修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 六轮修复定向 | `& $py -m pytest companion/server/tests/test_chat_cancel.py -q` | 10 passed |
| 六轮修复父提交复验 | worktree `a01bee41` + 本测试 | `test_generator_close_frees_conversation_lock_before_persisting` 失败于 `locked()` |
| 六轮修复全量 | `& $py -m pytest -q` | **402 passed / 0 failed** |
| 六轮修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **14/14 exit=1，hangs: none** |
| 七轮修复定向 | `& $py -m pytest companion/server/tests/test_audio_pipeline.py::test_cancel_in_thinking_does_not_cancel_the_next_turn_speaker -q` | 1 passed |
| 七轮修复父提交复验 | worktree `00395370` + 本用例 | 失败于 `_speaker_task is None`（新轮 speaker 被取消/清空） |
| 七轮修复全量 | `& $py -m pytest -q` | **403 passed / 0 failed** |
| 七轮修复变异 | `& $py companion/tests/mutation/audio_block_b.py` | **15/15 exit=1，hangs: none** |

### Task 7 覆盖的语音 e2e

| 场景 | 断言要点 |
| :--- | :--- |
| 正常轮次 | `voice.start` → 2 帧 → `voice.end`；收到 `voice.segment`、末帧 `FLAG_LAST`、模拟器自动 `segment_played`；App `turn_end(finish_reason=stop, interrupted=false)`；DB 有 USER + ASSISTANT 节点且 LLM 真被调用 |
| barge-in | 在途 `action.play` 时 `voice.cancel(barge_in)`；`cancel_ack`；`action.stop(barge_in)` 恰好一次（重复取消不再发）；`turn_end(interrupted=true)`；DB 助手消息 `interrupted=true` 且保留已生成文本 |
| App 占用对话锁 | 真实会话锁被持有 → `voice.error(busy, fatal=true)`，LLM 未被调用 |
| 未绑定 | `voice.error(unbound, fatal=true)`，LLM 未被调用 |
| 断线清理 | 生成中途断开设备 WS → App 收到 `device.status(online=false)`；DB 已生成部分标 `interrupted` |

模拟器自动回执规则：收到新 `voice.segment` 时回执上一段；收到带 `FLAG_LAST` 的末帧时回执当前段；`voice.cancel`/`voice.error` 清空待回执；`close()` 取消未完成的 ack 任务。回执延迟可配置（`segment_played_delay`），默认 0。

### 变异用例（15）

分句：`flush()` 丢弃尾句、`max_chars` 不硬切；下行：`FLAG_LAST` 不置位、`turn_seq` 不自增、段元数据晚于音频；管线：取消不递增代次、播放看门狗不启动、末段回执不结束轮次、旧轮清理取消新轮 speaker；落库：取消丢 `interrupted`；动作：`action.stop` 固定 `server_cancel`；绑定：角色回退取最旧会话、忽略已绑定会话；网关：`voice.cancel` 不回 ack、迟到取消不重放 ack。全部被对应用例捕获（exit=1），无超时。

提交：

| 提交 | 说明 |
| :--- | :--- |
| `46944557` | 流式分句器 |
| `96a22364` | Mock ASR/TTS |
| `dbd0bf7c` | 下行发送器 |
| `2e5261cc` | 完整 `VoiceSession` 管线 |
| `3784745d` | 取消落库与 `cancel_reason` 透传 |
| `cda10ebc` | 语音运行时接入设备网关（Task 6） |
| `8a50bbe0` | 语音管线真实 WS e2e、模拟器自动回执与协议文档（Task 7） |
| `test(companion): verify audio block B and record the migration log` | Task 8 回归、变异脚本与本日志（hash 以 `git log` 为准） |
| `fix(companion): close audio block B review findings` | 审查修复：旧轮清理隔离、end-before-begin、commit 中取消重试、末段空音频、回执/看门狗竞态 |
| `fix(companion): harden audio block B after second review` | 复审修复：落库幂等与取消等待、turn_start 计时器泄漏、shutdown 等待取消清理、`_fail` 终态顺序、零音频 TTS 用例 |
| `fix(companion): make turn persistence collision-safe` | 第三轮修复：每轮独立 `turn_key` 幂等键、落库锁内重算 `node_index`、取消时单次结算并取消在途任务、regenerate 判重 |
| `fix(companion): bound cancel persistence and scope persist locks` | 第四轮修复：取消落库统一 2s 预算、优先在会话锁内落取消轮、落库锁按事件循环隔离、补超时取消用例 |
| `fix(companion): keep barge-in restart available during cancel persistence` | 第五轮修复：取消落库不再持有会话锁（避免新轮 fatal busy）、落库锁内层改弱值、补“barge-in 后立即 voice.start”e2e |
| `fix(companion): release chat lock before persisting closed streams` | 第六轮修复：`GeneratorExit` 路径先 `aclose()` 内层再落库、e2e 断言 `turn_end(finish_reason=stop, interrupted=false)`、补 aclose 用例 |
| `fix(companion): scope speaker task to its own turn` | 第七轮修复：`_speaker_task` 改为本轮局部变量 + 身份校验，旧轮 finally 不再取消/清空新轮 speaker；downlink 按轮传递 |

## 5. 偏差与 Ruling

1. **`close_voice()` 调用 `shutdown()` 而非计划里的 `close()`**：Block B 需要取消 `begin()` 任务并有界等待清理；
   `shutdown()` 内部先同步 `close()`，保持 Block A 的同步语义不变，另提供异步有界收尾。
2. **`voice.start` 不等待 `begin()`**：解析会话要 await DB，不能阻塞读循环；失败通过 `voice.error` 异步返回。
   同一 `turn_id` 重复 `voice.start` 由 `begin()` 内部幂等挡掉。
3. **`runtime.py` 无需新增字段**：Task 4 已建立 `VoiceConfig/VoiceRuntime`，Task 6 只做装配。
4. **`send_control` 已在 Block A 存在**：Task 6 未重复添加，仅复用其发送锁。
5. **Block A 两条 e2e 暂时关闭 runtime**：`transport_only_voice` fixture 显式注入 `None`，
   否则默认 `unavailable` provider 会让纯传输断言改变语义；Task 7 用完整语音 e2e 覆盖后仍保留该 fixture 以隔离两层测试。
6. **`VOICE_PROVIDER=dashscope` 在 Block C 前降级为 unavailable**：避免使用未验证的云连接；日志只报 provider 名称，不含 Key。
7. **绑定测试使用 `store` fixture 名**：避免与 pytest 内置 `fixture` 概念混淆；用例结束按 device → conversation → assistant 顺序清理。
8. **`voice.end` 早于 `begin()` 完成时挂起开轮**：`voice.start` 只同步 `start()`，`begin()` 在事件循环下一拍才解析会话/开 ASR。设备（尤其模拟器）会在同一个 tick 发出 `voice.end`，原实现直接 `_start_turn_task()` 会因 `_asr is None` 误报 `asr_unavailable`。现在 `end_user_speech()` 在 `_begin_started and _asr is None` 时置 `_end_pending`，由 `begin()` 打开 ASR 后补开轮次；Block A（无 runtime）分支不变。新增 `test_end_before_begin_open_waits_for_asr_instead_of_failing` 锁定。
9. **模拟器自动回执只为“上一段”调度**：首个 `voice.segment` 到达时没有上一段，原实现会以 `None` 调度回执导致任务异常；改为 `previous is not None` 才调度。末帧回执当前段，保证 `segment_played(last)` 能结束轮次。
10. **断线 e2e 用可阻塞的假 LLM**：若假 LLM 一次吐完并立即 `done`，断线前回复可能已正常落库，无法验证 `interrupted`。断线用例改用 `GatedDeepSeek`，在发出部分文本后卡住，再断开设备 WS。
11. **旧轮清理必须先同步摘除资源**（审查 Blocker）：`_complete_turn`/`cancel_turn` 原来先置 IDLE、后清理，设备可在 `await` 窗口内开新轮，旧清理随后会关掉新轮 ASR/sender 并清空任务引用。现在新增 `_TurnResources` 快照：结束/取消时先同步摘除本轮 ASR、任务、sender、计时器，再在后台按快照清理；上行泵也捕获本轮的队列/事件。新增“旧清理卡在慢 `ASR.close()` 时开新轮”的完成/取消两例回归。
12. **`voice.end` 只要 ASR 未就绪就挂起**（审查 Blocker）：原条件 `_begin_started and _asr is None` 在 `begin()` 尚未被调度时仍会直接开轮，真实异步 resolver 下误报致命 `asr_unavailable`。改为 runtime 模式下 `_asr is None` 一律置 `_end_pending`；测试改用会 `await` 的 resolver，复现网关 `create_task(begin())` 后同 tick 收到 `voice.end` 的时序。
13. **落库 `saved` 标记后移到 commit 成功之后**（审查 Major）：取消恰好落在 `_save_turn()` 的 commit 中时，原实现因 `saved=True` 跳过重试，整轮可能丢失。现在 commit 返回后才置位；外层取消分支先 `rollback()` 再用独立任务重试。新增“commit 中取消”回归，断言用户节点与部分助手回复都落库且 `interrupted=true`。
14. **终态先同步占用再发错误**（审查 Minor）：播放/总时长看门狗原来先 `await` 发 fatal error 再完成轮次，与末段回执并发时可能给成功轮次补发错误。`_complete_turn` 现在先同步置 IDLE、递增代次、摘除资源，再按需发 `voice.error` 与 `turn_end`；新增看门狗占用时序回归。
15. **末段无音频也下发段文本**（审查 Minor）：`DownlinkSender` 新增 `flush()` 哨兵，`_speaker` 在结束前调用；补“末段无音频”测试。
16. **同一 `turn_id` 在任意活跃状态重复 `voice.start` 幂等**（审查 Minor）：原实现只在 LISTENING 幂等，THINKING/SPEAKING 会回 busy，与协议不符；改为 `ACTIVE_STATES` 内直接返回 True，不重置代次。
17. **commit 成功后的取消不重复落库**（复审 Major 残留，第三轮已修正实现）：`_save_turn()` 改为独立任务 + `shield`，取消分支只结算一次（成功即跳过重试，超时则 cancel + gather 后再回滚）。落库幂等键改为每轮独立的 `turn_key`（uuid4），节点/消息 ID 为 `uuid5(conversation_id + turn_key + 角色)`；**不再使用 `node_index` 或 `len(all_nodes)` 作为键**，因为取消落库期间新一轮可能读到旧节点集，按行数生成键会把合法新轮误判为重复并静默丢弃。
18. **`turn_start` 发布窗口不再泄漏计时器**（复审 Minor）：`begin()` 在 `await _emit("turn_start")` 后重新校验 `_turn_ok`；`_reset_turn_buffers()` 显式取消遗留 timer；资源摘除时清空 `_begin_started/_end_pending`。补“turn_start 期间取消”用例。
19. **`shutdown()` 等待进行中的取消清理**（复审 Minor）：同时有界等待 `_close_task` 与 `_cancel_task`，慢 `ASR.close()` 不会在关停后继续存活。
20. **`_fail()` 统一“先占终态、再发错误”**（复审 Minor）：改为调用 `_complete_turn(error_code=..., error_fatal=...)`，与看门狗并发时不会对同一轮双发 fatal。
21. **零音频 TTS 仍按 `tts_unavailable` 处理**（复审 Minor 5 边界）：`flush()` 只覆盖“段已开始但暂无音频、下一段/轮次结束时补发文本”；TTS 完全返回 0 包视为 provider 失败，不下发设备无法回执的 `last` 段，补管线级用例锁定该语义。
22. **落库下标在锁内重算并串行写入**（第三轮 Major）：新增会话级 `get_persist_lock()`，`_save_turn()` 在锁内用 `MAX(node_index)+1` 计算下标后再 `commit`。这样“取消轮落库未完成时开始的新一轮”即使 `build_context` 读到旧节点集，也不会写出重复 `node_index` 或互相覆盖；补“两个陈旧上下文并发落库”用例，断言最终下标为 `0,1,2,3` 且两轮都在。
23. **同一会话的 `AsyncSession` 不再被并发使用**（第三轮 Major）：取消时只调用一次 `_settle_save_task()`；超时先 `cancel()` 并 `gather` 在途任务，再 `rollback()` 重试，消除两个 `_save_turn` 同时操作同一 session 的窗口。
24. **重新生成的重试不再追加重复候选**（第三轮 Minor）：`regenerate` 分支用 `turn_key` 生成的助手消息 ID 判重，已存在则直接返回。
25. **绑定测试改为确定性**（变异回归）：`test_bound_conversation_wins_over_assistant` 原来两次创建可能落在同一毫秒，`updated_at` 相同时“显式绑定失效”的变异会偶然通过。现在显式设置 `updated_at=1000/2000`，保证回退候选更新，变异稳定被杀。
26. **取消落库使用统一预算**（第四轮 Minor）：原来“结算 2s + 重试 2s”最坏约 4s，超出音频侧 2s 后台清理窗口。现在 `_CANCEL_TOTAL_TIMEOUT=2.0` 统一 deadline，结算最多 1.5s，重试只使用剩余时间；补“commit 成功后落库协程超时”用例。预算是软上限：若在途任务对取消无响应，额外等待其收尾，避免并发使用同一 `AsyncSession`。
27. **取消落库不持有会话锁**（第五轮 Major 回归修复）：第四轮曾尝试在取消落库时持有会话锁以保证顺序，但 barge-in 时设备收到 `cancel_ack` 后会立刻 `voice.start`，`begin()` 把“落库中”误判为生成中并回 fatal `busy`，新轮被直接终止。现在取消路径完全不取会话锁，并在落库前先关闭内层生成器以释放它原本持有的锁（见 §5.28），只靠落库锁 + `MAX(node_index)+1` 保证不丢不重；消费端若在新轮开始后才完成落库，会出现“取消轮排在新轮之后”的顺序偏差（§6）。新增真实 WS e2e：`test_voice_start_right_after_barge_in_is_not_busy`。
28. **`GeneratorExit` 路径先关内层再落库**（第六轮 Minor，影响等同 Major）：`aclose()`/GC 关闭外层生成器时，`async for` 中断不会自动关闭内层，内层仍挂在 `async with lock` 的 `yield` 上；若先落库，会话锁会一直被占用，新轮仍 `busy`。现在 `except` 分支第一步 `await inner.aclose()`（幂等，`finally` 会再调用一次），确认锁释放后才结算/回滚/落库。补 `test_generator_close_frees_conversation_lock_before_persisting`（父提交上稳定失败于 `locked()` 断言）。
29. **落库锁按事件循环隔离**（第四轮 Minor，第五轮修正）：`_PERSIST_LOCKS` 为 `WeakKeyDictionary[loop, WeakValueDictionary[conv_id, Lock]]`。内层必须用弱值，否则 `Lock._loop` 强引用 key，外层 WeakKeyDictionary 永远无法回收。会话锁仍保持进程级全局，因为语音运行时/App 会在不同线程检查 `.locked()`（e2e 依赖该可见性）。
30. **`_save_turn` 的 `all_nodes` 仅作“已建上下文”信号**（第四轮 Nit）：节点下标不再依赖它；保留参数避免改动全部调用点，实际计算全部走 `MAX(node_index)+1`。
31. **重试命中已提交节点时不改写 `interrupted`**（第四轮 Nit）：若正常轮在 commit 后才被取消，重试直接复用已提交消息，不把成功轮改成中断轮；这是有意语义，已在日志说明。
32. **barge-in e2e 断言收尾语义**（第六轮 Nit）：`turn_end` 在 fatal busy 时也会发出，因此新增用例改为断言 `finish_reason == "stop"`、`interrupted is False`，不再只等 `turn_end`。
33. **speaker 任务按轮隔离**（第七轮 Major，既有问题）：`_generate_and_speak()` 原来把 speaker 存到共享属性 `self._speaker_task`，并在 `finally` 里取消/清空它；旧轮在 THINKING 被取消后，其 `finally` 可能在新轮已建好 speaker 之后才执行，从而取消新轮任务并把属性置空，新轮 `await None` → fatal `internal_error`。现在 speaker 由本轮局部变量持有，`finally` 只取消自己的任务，且仅在 `self._speaker_task is speaker_task` 时才清空；`_speaker`/`_synthesize`/`_enqueue_packet` 改为显式接收本轮 `DownlinkSender`，避免旧轮读取新轮 sender。补确定性用例 `test_cancel_in_thinking_does_not_cancel_the_next_turn_speaker` 与第 15 个变异用例（父提交 `00395370` 稳定失败）。

## 6. 已知限制

- 全部 provider 为 Mock/Unavailable，**未验证真实 DashScope ASR/TTS 的协议细节与音频质量**。
- 未在真机上验证播放回执、barge-in 时延、设备端缓冲与断线行为。
- Block A 遗留 minor（模拟器 10 次/秒拒绝、`max_duration_ms` 看门狗、越界角度截断）仍未处理。
- 单 worker 限制、Android 对 1011/1012 与重复 offline 的处理仍未验证。
- **已知顺序偏差**：取消落库不持有会话锁，若新一轮先提交，被取消的提问会排在新轮问答之后（节点下标仍唯一、不丢轮）。常见毫秒级落库下顺序正常。
- 取消落库预算是软上限：若在途任务对 `cancel()` 无响应（如驱动忽略取消），会额外等待其收尾，最坏超过 2s。
- `_CONVERSATION_LOCKS` 为进程级全局字典，不随事件循环回收；多循环测试进程内会累积已关闭循环的锁，生产单循环无影响。

## 7. 下一步

1. Block B 已完成八轮独立审查：Blocker/Major/Minor 均已修复并补回归，修复记录见 §5.11–§5.33；第八轮复审通过，无新问题（建议的 THINKING 阶段 WS e2e 留作后续增强）。
2. 进入 Block C：真实 DashScope 适配器（ASR/TTS），只对本地假服务器验证；真实云与真机验收标记为待办。
3. Block C 完成后补 Android App 侧对齐与真机音频验收（播放回执、barge-in 时延、5 分钟沉默自动结束）。
