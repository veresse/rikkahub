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
