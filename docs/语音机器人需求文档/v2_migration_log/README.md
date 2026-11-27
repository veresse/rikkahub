# v1 → v2 迁移改造日志（索引）

本目录用于记录「实体语音陪伴机器人（第二代）」迁移过程中每一次代码修改、测试结果、决策与遗留问题，便于后续代码管理与审核。

## 约定

- 每次修改一个「区块（Block）」写一份日志：`NN_<block>.md`，编号递增。
- 每份日志必须包含：
  1. 目标与范围；
  2. 改动文件清单（新增/修改/删除）；
  3. 实际执行的命令与真实输出（通过/失败/跳过及原因）；
  4. 与计划的偏差及原因（Ruling）；
  5. 未完成项/阻塞点；
  6. 下一步。
- 不在日志中记录任何真实 API Key、管理员密码、Bearer Token、Wi-Fi 密码、备份正文或私密聊天内容。
- 迁移总方案：`docs/语音机器人需求文档/v1_v2.md`。
- v1 代码审计：`companion/docs/v1_to_v2_audit.md`。

## 日志列表

| 编号 | 区块 | 状态 | 说明 |
| :--- | :--- | :--- | :--- |
| 01 | Phase 0：仓库基线审计与分支隔离 | ✅ 已完成（待负责人确认范围） | 分支 `feature/companion-robot-v2`；审计报告 + pytest/Android 构建基线 |
| 02 | Block A（Phase 1）：动作库与 protocol v2 定稿 | ✅ 已完成 | `actions.json` / JSON Schema / 校验器 / `protocol_v2.md`；38 passed |
| 03 | Block B（Phase 2 基础）：鉴权 + Alembic + 删除网页端 | ✅ 已完成 | Bearer Token/配对码/设备 Token 哈希/可逆迁移；64 passed |
| 04 | Block C（Phase 2 核心）：动作编排 + DeepSeek 工具循环 | ✅ 已完成 | 5 个 `robot.*` 工具、单轮限次/排队/限速/覆盖/急停、`tool_result` SSE；93 passed |
| 05 | Block D-1：设备 WebSocket 网关 | ✅ 已完成 | `/device/ws` 握手/v1 降级/会话替换/回执关联/上报落库；120 passed |
| 06 | Block D-2：App WebSocket 网关与超时强停 | ✅ 已完成 | `/app/ws` 遥控/广播/撤销断开；超时下发 `action.stop`；133 passed |
| 07 | Phase 2 审查修复（Block C / D-1 / D-2） | ✅ 已完成 | 终态事件、在途超时、v1 门控、撤销竞态、上报 writer、设备撤销断连；143 passed |
| 08 | Phase 2 二次审查修复 | ✅ 已完成 | 终态去重、取消只停本轮、覆盖优先于容量、上报冲刷、后台广播；153 passed |
| 09 | Phase 2 三次审查修复 | ✅ 已完成 | 上报 FIFO 冲刷、双向终态恰好一次、撤销设备回退、4429 可达；160 passed |
| 10 | Phase 2 四次审查修复 | ✅ 已完成 | 反向终态窗口、在途上报补写、回归测试有效性、角色级撤销回退；166 passed |
| 11 | Phase 3：双模拟器与真实 WebSocket 端到端验收 | ✅ 已完成 | `device_simulator` / `app_simulator`、13 例真实 uvicorn/WS 矩阵、修复新建会话嵌套事务；181 passed |
| 12 | Phase 3 审查修复 | ✅ 已完成 | 撤销/替换前冲刷上报、`stop_writer()` 串行化、模拟器 CLI 常驻与真机断连语义、LWW/限速/日志用例改成有效断言；186 passed |
| 13 | Phase 3 二次审查修复 | ✅ 已完成 | 撤销窗口 `closing` 门控、已停用设备不回写运行态、写失败不再毒化冲刷、CLI 失败退出码、文档矛盾；190 passed |
| 14 | Phase 3 三次审查修复 | ✅ 已完成 | 撤销与在途落库/迟到 hello 的竞态、`closing` 对外视为离线、已撤销设备不再广播运行态、回归用例有界失败；193 passed |
| 15 | Phase 3 四次审查修复 | ✅ 已完成 | `hello_ack` 前 `ready` 门控与上线广播后移、统一在线判定（LLM/事件/App）、`_install()` 锁内复核回归、hello 落库条件更新、`closing` 急停结算本地动作；198 passed |
| 16 | Phase 3 五次审查修复 | ✅ 已完成 | ack 阻塞期间撤销不再广播上线、App 网关同时拦未 ready、orchestrator 在线判定回归、争锁死锁有界失败、移除重复在线函数；201 passed |
| 17 | Phase 3 六次审查修复 | ✅ 已完成 | 撤销提交与关闭串行化、`ready` 后移到复核之后、offline reason 统一、三道守卫独立回归；204 passed |
| 18 | Phase 3 六次审查收尾 | ✅ 已完成 | 停机补发 `server_shutdown` 下线事件、新用例按握手阶段定位并提供诊断、删除弱断言与旧注释；205 passed |
| 19 | Phase 3 停机链路 | ✅ 已完成 | `CompanionServer` 在 uvicorn 关连接前下线、停机标志拒绝并发注册、offline 原因保留先到者、异常路径进锁、真 uvicorn 停机 e2e；211 passed |
| 20 | Phase 3 停机收尾 | ✅ 已完成 | `run()` 恢复 reload 分支并拒绝多 worker、应用层停机 10s 总时限与 `force_exit`、App 侧停机门控、强化 M-1 用例；220 passed |
| 21 | Phase 3 停机总预算 | ✅ 已完成 | 10s + 2s + 2s + 20s 四层预算、lifespan 有界兜底、中窗 `force_exit` 轮询、取消可重试与逐台 offline、真实 TCP 1011 e2e、mutation 入库；228 passed |
| 22 | Phase 3 停机（三）：offline 补偿 | ✅ 已完成 | 取消越过 unregister 后靠 `pending_offline_reason` 补发、`_teardown()` 取消回填、offline at-least-once、测试隔离与 mutation 超时区分；231 passed |
| 23 | 音频 Block 设计（ASR/TTS/barge-in） | ✅ 设计已批准 | 协议补齐、纯 Python Ogg、VoiceSession/打断/落库/超时方案；子块 A→B→C |
| 24 | 音频 Block A：协议 / 帧 / Ogg / 传输 | ✅ 已完成（已审查修复） | 12B 帧、Ogg mux/demux、`send_bytes/send_audio`、VoiceSession 门控、模拟器二进制、协议 §4.8/§5.4；364 passed、mutation 17/17 |
| 25 | 音频 Block B：Mock 管线 / 打断 / 落库 / 网关接线 | ✅ 已完成（八轮审查修复） | 流式分句、Mock ASR/TTS、下行节流、完整轮次、barge-in、取消落库、运行时注入、6 例真实 WS 语音 e2e；旧轮清理隔离、每轮 `turn_key` 幂等落库、落库锁内重算下标、取消预算、barge-in 后可立即重开、GeneratorExit 先释放锁、speaker 按轮隔离等修复；403 passed、mutation 15/15 |
| 26 | 音频 Block C：真实 DashScope 适配器 | ✅ 已完成（三轮审查修复；真实云/真机待办） | ASR WebSocket `fun-asr-realtime`、TTS HTTP SSE `qwen-audio-3.0-tts-flash`、URL/配置/区域、缺 Key 降级、烟测脚本；本地假 WS/SSE 覆盖握手/上行/final/finish 超时/断线/close 竞态/连接异常与完整轮次；**448 passed**、mutation 15/15 + 9/9；真实云与真机未验证 |
| 27 | Phase 4-4A：Android 工程/登录/加密会话 | ✅ 已完成（两轮审查修复；真机待办） | 独立单模块工程、强制 TLS + debug 本地明文、Keystore AES-GCM 会话、401 清退、429 `Retry-After` 倒计时；App **53 例** + 服务端 4 例；**452 passed**、debug/release 构建成功；端口/点段/IPv4 加固、`device_name`、截止时刻倒计时、Mutex、按 Token 去重（修二次登录 401 失聪）等 11 项 + P1 回归修复；真机/HTTPS 未验证 |
| 28 | Phase 4-4B（上）：服务端 + App 基础设施 | ✅ 已完成（含 App 审查修复；Task 9 见 29） | 角色 `model_id`/`web_search_mode`、`client_msg_id` 幂等、`turn_state` 广播、转写纠正 PUT；App REST 仓库/SSE/WS/角色页；SSE `Call.cancel()` 真断连、120s 空闲超时、离线门控、WS 30s 稳定退避、401 按 Token 去重、`avatar` 保全；服务端 **425 passed**、App **100 例**、双构建成功 |
| 29 | Phase 4-4B（下）：App 会话列表 / 流式聊天 / 记忆 / 导航作用域 | ✅ 已完成（三轮 App 审查修复；真机待办） | 按路由 `ViewModelStore`（弹栈即取消 SSE）、导航栈与 Token 混合恢复、`isOnline` 写守卫、SSE 终态后 IO 抑制与 429 倒计时、WS 断线清 `turnStates`、写请求在途禁止弹栈、列表代次防「复活」、服务端 `turn_end` 补 `conversation_id` 且先发事件再清理、`ErrorMapper` 文案收敛；App **19 suites / 137 例**、服务端 **425 passed**、debug/release 构建成功；真机/真实流式未验证 |
| 30 | Phase 4-4C：设备列表 / 遥控 | ✅ 已完成（三轮审查修复；真机待办） | 服务端 `GET /api/devices` 每项 `online`（撤销即离线）；App WS 可写句柄与 `send()`、重连清设备缓存/重拉快照；设备页（配对码倒计时/改名/绑定/撤销墓碑/WS 在线合并与 REST 回退）；遥控命令构造与 200ms 防抖、ack≠成功（按 `action_id` 等 `action.result`）、急停结算在途动作、5s ack / 15s 软超时 / 45s 硬结算、校准草稿冻结、离线门控、结果条按动作序号裁决；App **24 suites / 223 例**、服务端 **427 passed**、debug/release 构建成功；真机与真实舵机未验证 |
