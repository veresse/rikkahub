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
