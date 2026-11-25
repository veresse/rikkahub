# 01 · Phase 0：仓库基线审计与分支隔离

- 日期：2026-11-25
- 状态：✅ 审计完成（范围决策待负责人确认）
- 基线分支：`feature/companion-server`
- 基线 commit：`98a7d15647bbcc05af8ca1f7cabf2877655ef011`
- 工作分支：`feature/companion-robot-v2`

## 1. 目标

按 `v1_v2.md` Phase 0 要求，摸清当前仓库真实代码基线，产出迁移映射表，并记录 v1 自动化测试与 Android 构建的真实结果。**本阶段不写业务代码。**

## 2. 已完成项

1. 创建并切换到新分支 `feature/companion-robot-v2`（从 `feature/companion-server` 切出）。
2. 审计 `companion/` 下全部 58 个文件，逐项分类为「保留 / 修改 / 新增 / 删除」。
3. 对照第二版方案，识别出当前 v1 与服务端迁移方案假设之间的关键缺口。
4. 执行 v1 `pytest` 基线并记录真实结果。
5. 执行根目录 Android `assembleGithubDebug` 基线并记录真实失败原因。

## 3. 改动文件清单

| 类型 | 路径 | 说明 |
| :--- | :--- | :--- |
| 新增 | `companion/docs/v1_to_v2_audit.md` | v1 现有实现盘点与 v2 迁移映射表 |
| 新增 | `docs/语音机器人需求文档/v2_migration_log/README.md` | 迁移日志索引与约定 |
| 新增 | `docs/语音机器人需求文档/v2_migration_log/01_phase0_baseline.md` | 本文件 |

本阶段未修改任何业务代码。

## 4. 实际执行的命令与真实输出

### 4.1 分支

```text
$ git switch -c feature/companion-robot-v2
Switched to a new branch 'feature/companion-robot-v2'

$ git branch --show-current
feature/companion-robot-v2

$ git rev-parse --short HEAD
98a7d156
```

### 4.2 Python 测试基线

系统 Python 缺少 `aiosqlite`，直接运行报 collection 错误：

```text
$ python -m pytest -q
ERROR companion/server/tests/test_context_optimizer.py
ERROR companion/server/tests/test_full_system.py
ERROR companion/server/tests/test_importer.py
E   ModuleNotFoundError: No module named 'aiosqlite'
3 errors during collection
```

使用隔离 venv 安装 `requirements.txt` 后：

```text
$ uv venv "...\opencode\companion-venv" --python 3.11
$ uv pip install --python "...\companion-venv\Scripts\python.exe" -r companion/server/requirements.txt
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
........                                                                 [100%]
8 passed in 14.23s
```

**结论：v1 Python 测试基线 = 8 passed。** 覆盖范围仅限：导入器解析/导入、阶梯滞回上下文、端到端文字/分支/记忆/配对码/导出。

### 4.3 Android 根工程基线

```text
$ gradlew --version
Gradle 9.6.0
Launcher JVM: 17.0.21 (Eclipse Adoptium 17.0.21+9)
OS: Windows 10 10.0 amd64

$ gradlew assembleGithubDebug --console=plain
> Task :app:processGithubDebugGoogleServices FAILED
* What went wrong:
Execution failed for task ':app:processGithubDebugGoogleServices'.
> No matching client found for package name 'me.rerere.rikkahub.debug' in app\google-services.json
BUILD FAILED in 49s
```

**结论：Android 基线构建未通过**，原因是 `app/google-services.json` 中缺少 `me.rerere.rikkahub.debug` 的 Firebase client 配置。这是仓库既有配置问题，与本次迁移无关；本阶段未修改根 Android 工程。

## 5. 关键发现（阻塞项）

当前 v1 实际实现远小于迁移方案假设：

- 没有 `/device/ws` 后端实现与 `companion.v1` 协议；
- 没有 ASR/TTS/Opus 语音链路；
- 没有管理员登录与任何鉴权；
- 没有 Alembic 迁移（仅 `create_all`）；
- 没有 LLM 工具调用聚合与执行；
- 没有 `companion/shared`、`companion/app`、`companion/firmware`、`companion/tests`。

详见 `companion/docs/v1_to_v2_audit.md` 第 3 节。

## 6. 与计划的偏差 / 待决策

| # | 问题 | 建议 |
| :--- | :--- | :--- |
| 1 | 本轮是否包含 Android App 与 ESP32 固件？（仓库中两者均不存在） | 先只做服务端，App/固件独立立项 |
| 2 | 是否本期要求「真机可对话」？（需要从零补设备 WS + ASR/TTS + 状态机） | 先做控制面（鉴权/动作/网关/模拟器），音频链路作为独立 Block |
| 3 | Android App 未就绪时是否立即删除 `companion/web`？ | 先保留网页端，待 App 可用后再删 |
| 4 | 加 Bearer 鉴权会同时影响现有网页端，是否同步改造？ | 同步改造（推荐），避免长期裸奔 |
| 5 | 是否允许使用隔离 venv/Node 工具跑测试（不写入仓库）？ | 已使用临时目录 venv，未污染仓库 |

## 7. 下一步

等待负责人确认第 6 节范围后，进入 **Block A（Phase 1）：`companion/shared/actions.json` + `companion/docs/protocol_v2.md` + JSON Schema 校验测试**。
