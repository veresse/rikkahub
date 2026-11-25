# v1 现有实现盘点与 v2 迁移映射表（Phase 0）

> 生成时间：2026-11-25
> 审计基线分支：`feature/companion-server`
> 审计基线 commit：`98a7d15647bbcc05af8ca1f7cabf2877655ef011`
> 迁移目标分支：`feature/companion-robot-v2`
> 对照文档：`docs/语音机器人需求文档/v1_v2.md`、第二版《实体语音陪伴机器人（第二代）技术实现与执行方案》

---

## 0. 结论摘要（先看这里）

**当前仓库的 v1 与迁移方案假设的 v1 存在明显规模差异。** 迁移方案（`v1_v2.md`）按「已具备 FastAPI + SQLite + 导入 + 搜索 + ASR/TTS + `DeviceGateway` + `companion.v1` 音频协议 + `companion/firmware` + `companion/tests`」来规划；实际仓库中：

- **已具备**：SQLite 数据模型、RikkaHub 备份导入（预览/确认/导出）、角色/会话/记忆/分支 CRUD、DeepSeek 流式文字对话（SSE）、Bing 网页搜索适配器、设备配对码生成与设备表的**占位实现**、React 网页管理端、Nginx/systemd 部署脚本。
- **完全缺失**：设备 WebSocket 网关（`/device/ws` 只有 Nginx 配置，没有后端路由）、`companion.v1` 音频协议与状态机、ASR/TTS/Opus 语音链路、管理员登录与任何鉴权、Alembic 迁移、LLM 工具调用聚合与执行、`ActionOrchestrator`、`AppGateway`、`companion/app`、`companion/firmware`、`companion/shared`、`companion/tests`、`companion/docs`。

因此，v2 不是「在既有音频/设备链路上加动作」，而是需要**从零补齐设备网关与语音链路**（如果本期要求真机可对话）。这一点必须先由项目负责人确认范围，再进入编码。

---

## 1. 仓库与工程基线

| 项目 | 实际值 |
| :--- | :--- |
| 当前分支 | `feature/companion-server` |
| HEAD | `98a7d15647bbcc05af8ca1f7cabf2877655ef011`（`feat(companion): 增加应用更新代理路由 /api/app-update`） |
| 许可证 | 根目录 `LICENSE` = AGPL-3.0 |
| 根工程 | RikkaHub 多模块 Android 工程，`settings.gradle.kts` 包含 `:app :ai :search :speech :common :document :web :material3 :ui :workspace :terminal :app:baselineprofile :mediagen :oauth` |
| Android 构建前置 | `app/google-services.json` 存在；`local.properties` 指向 `D:\Android\sdk`；`gradlew --version` 正常（Gradle 9.6.0 / JDK 17.0.21） |
| companion 目录 | 仅 `deploy/`、`server/`、`web/` 三个子目录，共 58 个文件 |
| Python | 3.11.15（本机）；`requirements.txt` 未锁定精确版本 |
| 根 `pytest.ini` | `pythonpath = .`、`asyncio_default_fixture_loop_scope = function` |

---

## 2. `companion/` 现有文件逐项盘点

### 2.1 `companion/server`（FastAPI 服务端）

| 路径 | 现状 | 与 v2 的关系 |
| :--- | :--- | :--- |
| `app/main.py` | 创建 FastAPI；注册 assistants / conversations / memories / chat / import_export / devices / app_update 路由；`/api/health`；若 `companion/web/dist` 存在则用 `StaticFiles` 挂载到 `/` | **需改**：移除静态前端挂载；新增 `/device/ws`、`/app/ws`、`/api/auth/*`、`/api/actions/catalog`；增加启动/关闭任务清理 |
| `app/config.py` | pydantic-settings；`SECRET_KEY` 每次启动随机（重启即变）；`ADMIN_PASSWORD` 未使用；DeepSeek/DashScope 只有配置项，没有实现 | **需改**：密钥持久化、管理员口令、App token 有效期、动作限流/时长上限、Alembic 开关、CORS 收敛 |
| `app/database.py` | async SQLAlchemy + aiosqlite；WAL / foreign_keys / busy_timeout=15000；`Base.metadata.create_all` 建表 | **需改**：引入 Alembic；保留 WAL/外键/繁忙超时；`create_all` 仅用于测试或移除 |
| `app/models/assistant.py` | 角色表：name / avatar / system_prompt / temperature / top_p / context_message_limit / enable_memory / use_global_memory / enable_web_search / model_id / raw_snapshot | **需增列**：`enable_action_sync`（v2 动作联动开关） |
| `app/models/conversation.py` | 会话表：assistant_id / title / is_pinned / custom_system_prompt / created_at / updated_at / raw_config | 保持；v2 的模型/搜索等配置仍放 `raw_config` 或后续扩展 |
| `app/models/message_node.py` | 树状分支节点：conversation_id / node_index / messages(JSON) / select_index | 保持（v1 回归核心） |
| `app/models/memory.py` | 记忆表：assistant_id 可空（空=全局）/ path / content / updated_at | 保持 |
| `app/models/device.py` | 设备表：name / `token`（**明文**）/ bound_assistant_id / bound_conversation_id / is_active / created_at / last_seen_at | **需改**：token 改哈希存储；新增 `protocol_version`、`capabilities_json`、`volume`、`idle_enabled`、`pan_offset`、`tilt_offset`、`last_report_json`、`state`；`last_seen_at` 类型统一 |
| `app/routers/assistants.py` | 角色列表/详情/新建/更新/删除；无鉴权 | **需改**：Bearer 鉴权；补 `enable_action_sync`；补动作联动字段 |
| `app/routers/conversations.py` | 会话列表/新建/详情（含节点树）/切换分支/删除；无鉴权 | **需改**：Bearer 鉴权；补并发冲突错误码（409） |
| `app/routers/memories.py` | 记忆列表/新建/更新/删除；无鉴权 | **需改**：Bearer 鉴权 |
| `app/routers/chat.py` | `POST /api/chat/completions`，SSE 流式返回；无鉴权 | **需改**：Bearer 鉴权；支持 `client_msg_id` 幂等；接入工具调用循环与动作编排；取消/断连处理 |
| `app/routers/import_export.py` | `/api/backup/preview`、`/confirm`、`/export`；ZIP 校验（大小/zip-slip/压缩炸弹）；预览缓存仅内存 | **需改**：Bearer 鉴权；补敏感字段脱敏报告、坏包回退说明；`/api/import/*` 路径与 App 对齐（可保留旧路径兼容） |
| `app/routers/devices.py` | 仅 `pair-code`（6 位码，内存 5 分钟）、`GET /devices`、`PUT /bind`、`DELETE /{id}`；**没有兑换配对码接口、没有签发 device_token、没有 WS** | **需重写/扩展**：配对码落库或带签名、限时/限次/限速；`POST /api/devices/pair` 兑换高熵 token；重命名；撤销；在线状态；离线原因 |
| `app/routers/app_update.py` | 代理 GitHub Release（`veresse/rikkahub`）给手机端检查更新 | 保持（若保留原 App 更新用途）；不属于 v2 核心 |
| `app/services/deepseek_client.py` | 流式请求 DeepSeek；可传 `tools`；会 yield `tool_call` 增量（**未聚合、未执行**） | **需改**：流式 tool-call 聚合、工具执行循环、结果回填、异常/超时 |
| `app/services/orchestrator.py` | `ChatOrchestrator`：系统提示词 + 记忆 + 历史分支 + 用户输入；阶梯滞回上下文截断；会话锁；搜索意图触发；流式落库 | **需改**：接入 `ActionOrchestrator`；设备状态上下文；工具注入；生成中动作并行；单会话锁保持 |
| `app/services/search_provider.py` | Bing HTML 解析 + SSRF 黑名单（私网/回环/localhost） | 保持；补更严格的 DNS 解析后校验与公网 URL 白名单 |
| `app/importer/validator.py` | ZIP 大小/解压体积/zip-slip 校验；要求 settings.json 或 rikka_hub.db 至少一个 | 保持并补测试 |
| `app/importer/parser.py` | 解析 RikkaHub settings.json / rikka_hub.db（角色、会话、message_node、memory_file），记录不支持项 | 保持；v2 无需改导入格式 |
| `app/importer/service.py` | 预览缓存（内存）+ 单事务导入；重复导入采用 upsert | **需改**：预览缓存加 TTL/上限；补失败回滚验证；记忆重复导入目前会重复插入（需修） |
| `app/routers/__init__.py` | 不存在（依赖隐式命名空间包） | **建议新增**显式 `__init__.py` |
| `app/services/__init__.py` | 不存在 | **建议新增** |
| `requirements.txt` | fastapi/uvicorn/pydantic/sqlalchemy/aiosqlite/httpx/pyjwt/alembic/bs4/pytest 等 | 保持；补 `websockets`（已由 uvicorn[standard] 带入）、`cryptography`（如需 HMAC/加密）、`pytest-cov` 可选 |
| `tests/test_full_system.py` | 端到端：health、网页静态页、导入预览/确认、角色、会话分支、记忆、配对码、导出 | **需改**：删除网页静态断言；改为 Bearer 鉴权；新增 v2 用例 |
| `tests/test_importer.py` | 解析器 + 导入服务单测 | 保持并扩展 |
| `tests/test_context_optimizer.py` | 阶梯滞回上下文算法单测 | 保持 |
| `tests/verify_standalone.py` | 独立导入器验证脚本（unittest） | 保持 |

### 2.2 `companion/web`（React + Vite 管理端）

| 路径 | 现状 | 与 v2 的关系 |
| :--- | :--- | :--- |
| `web/src/**` | 对话、角色、记忆、备份、设备 5 个视图；直接 `fetch('/api')`；无登录 | **计划删除**（v2 由 Android App 替代）。删除前需确认管理入口不中断 |
| `web/dist/**` | 已构建产物，被服务端 `StaticFiles` 挂载 | **计划删除**；同时移除 `main.py` 的静态挂载 |
| `web/package.json` 等 | Vite/Tailwind 构建配置 | 随 `web/` 一起删除 |

### 2.3 `companion/deploy`

| 路径 | 现状 | 与 v2 的关系 |
| :--- | :--- | :--- |
| `deploy/nginx.conf` | 监听 80/8080/9090；`location /` 全量反代到 8000（含静态页）；已有 `/device/ws` 升级配置 | **需改**：删除静态站点托管语义；新增 `/app/ws`；保留 `/api`、`/device/ws`；补安全响应头、上传限制、超时 |
| `deploy/deploy.sh` | 安装依赖、venv、`chmod web/dist`、systemd、nginx | **需改**：移除 web/dist 权限步骤；补 Alembic 升级、备份、回滚提示 |
| `deploy/companion.service` | root 运行、`MemoryMax=800M`、单 worker | **需改**：改为非 root（v2 文档要求）；保留单 worker（SQLite 单写） |

### 2.4 缺失目录（v2 方案要求但仓库中不存在）

| 目录 | v2 要求 | 现状 |
| :--- | :--- | :--- |
| `companion/shared/actions.json` | 动作库单一事实源 | **不存在** |
| `companion/docs/protocol_v2.md` | v2 协议规范 | **不存在** |
| `companion/docs/*` 六份手册 | 硬件/校准/固件/App/部署/许可 | **不存在** |
| `companion/app` | 独立 Gradle Android App | **不存在** |
| `companion/firmware` | ESP32-S3/ESP-IDF 固件 | **不存在** |
| `companion/tests/simulators` | device_simulator v2 / app_simulator | **不存在**（测试在 `server/tests`） |

---

## 3. 与迁移方案假设的关键差异（阻塞项）

1. **没有设备连接层**：`/device/ws` 无后端实现，`companion.v1` 的 hello/音频帧/状态机不存在。v2 的 `action.play`、`servo.move`、`volume.set`、`device.report` 没有承载通道。
2. **没有语音链路**：无 ASR/TTS 适配器、无 Opus 编解码、无分段 TTS、无打断/`segment_played`/5 分钟超时。v2 方案第 11 章与「音量 0 仍走完状态机」依赖这套机制。
3. **没有鉴权**：所有 REST 接口公开；`SECRET_KEY` 每次启动随机；`ADMIN_PASSWORD` 未使用；设备 token 明文存储；配对码只生成不可兑换。v2 的 App Bearer Token、device_token 隔离、撤销即断连均需从零实现。
4. **没有数据库迁移体系**：无 `alembic.ini` / `migrations/`，仅 `create_all`。v2 要求的可逆迁移、v1 存量库升级需新建。
5. **没有 LLM 工具执行**：DeepSeek 客户端能透传 `tools` 并吐出 tool_call 增量，但 orchestrator 不聚合、不执行、不回填；也没有搜索工具注入（当前搜索是关键词启发式直调）。
6. **没有 App/固件工程**：v2 Phase 4/5 的交付物在仓库中完全不存在，需要独立立项，工作量远大于服务端增量改造。
7. **设备配对码实现不安全/不完整**：内存字典、无过期清理、无兑换接口、无尝试次数限制；`DeviceModel.token` 明文且无创建入口。
8. **`companion/web` 仍在提供管理能力**：若按方案立即删除且 Android App 尚未交付，将出现「无管理入口」的空窗期。

---

## 4. 迁移映射表（保留 / 修改 / 新增 / 删除）

### 4.1 保留（不改或仅小改）

- 根目录 RikkaHub Android 工程（`app/ ai/ common/ ui/ speech/ search/ settings.gradle.kts` 等）：**严格只读**。
- `companion/server/app/importer/*`：导入格式与校验逻辑保留，补测试与 TTL。
- `companion/server/app/models/{assistant,conversation,message_node,memory}.py`：保留，仅 `assistant` 增列。
- `companion/server/app/services/search_provider.py`：保留并加固。
- `companion/server/tests/{test_importer,test_context_optimizer,verify_standalone}.py`：保留。
- `companion/server/app/routers/app_update.py`：按需保留。

### 4.2 修改（服务端增量升级）

- `config.py`、`database.py`、`main.py`
- `models/device.py`（扩展 + token 哈希）
- `routers/{assistants,conversations,memories,chat,import_export,devices}.py`
- `services/{deepseek_client,orchestrator}.py`
- `deploy/{nginx.conf,deploy.sh,companion.service}`
- `tests/test_full_system.py`
- `companion/README.md`

### 4.3 新增

- `companion/shared/actions.json` + JSON Schema + 校验脚本
- `companion/docs/protocol_v2.md`
- `companion/server/alembic.ini` + `companion/server/migrations/`（可逆）
- `companion/server/app/models/app_token.py`
- `companion/server/app/routers/auth.py`、`companion/server/app/routers/actions.py`
- `companion/server/app/security/{tokens.py,rate_limit.py,password.py}`
- `companion/server/app/services/{action_catalog.py,action_orchestrator.py,device_gateway.py,app_gateway.py,asr_provider.py,tts_provider.py,audio_pipeline.py}`
- `companion/server/app/protocol/{v1.py,v2.py}`（消息模型与校验）
- `companion/tests/simulators/{device_simulator.py,app_simulator.py}`
- `companion/tests/test_*.py`（动作/双源仲裁/鉴权/协议）
- `companion/server/tests/conftest.py`（隔离数据库、ASGI client、mock 云服务）
- 根 `pyproject.toml` 或 `companion/server/pyproject.toml`（测试/格式配置，可选）

### 4.4 删除（按确认后的范围执行）

- `companion/web/**`（含 `dist`）
- `main.py` 中的静态挂载
- `deploy.sh` 中 `chmod web/dist`
- `nginx.conf` 中「FastAPI 统一托管前端静态页面」注释与 `/` 静态语义
- `test_full_system.py` 中网页静态页断言

---

## 5. 测试基线（真实执行记录）

本机使用隔离 venv（不污染仓库）：

```powershell
uv venv "C:\Users\ctr\AppData\Local\Temp\opencode\companion-venv" --python 3.11
uv pip install --python "...\companion-venv\Scripts\python.exe" -r companion/server/requirements.txt
& "...\companion-venv\Scripts\python.exe" -m pytest -q
```

结果：

```text
........                                                                 [100%]
8 passed in 14.23s
```

说明：

- 直接使用系统 Python 时缺少 `aiosqlite`，3 个测试模块在 collection 阶段报 `ModuleNotFoundError: No module named 'aiosqlite'`；这是环境问题，不是代码失败。
- 8 个通过用例 = `test_full_system.py`(1) + `test_importer.py`(2) + `test_context_optimizer.py`(5)。
- `verify_standalone.py` 是独立脚本，不被 pytest 收集。
- 现有测试覆盖的是文字聊天/导入/分支/记忆/配对码，**没有任何设备 WS、音频、鉴权、动作、双源仲裁测试**。

Android 根工程基线：`gradlew --version` 通过（Gradle 9.6.0 / JDK 17）。`assembleGithubDebug` 的执行结果见 `docs/语音机器人需求文档/v2_migration_log/01_phase0_baseline.md`（构建耗时较长，单独记录真实输出）。

---

## 6. 需要项目负责人确认的范围问题

1. **本轮交付范围**：仅服务端（推荐先做），还是同时包含 `companion/app`（Android App）和 `companion/firmware`（ESP32 固件）？
2. **语音链路**：v1 并不存在设备音频/ASR/TTS。本期是否要求「真机可对话」？若是，需要新增一个「音频链路」阶段（设备 WS + Opus + ASR/TTS + 状态机），工作量大于动作控制本身。
3. **网页端处置**：Android App 尚未存在时是否立即删除 `companion/web`？推荐先保留并加登录鉴权，待 App 可用后再删。
4. **鉴权上线方式**：现有 REST 全开放。加 Bearer 鉴权会同时影响现有网页端。是否同意同步改造网页端（推荐），还是先只保护新增的 `/app/ws` 并保留只读/内网限制作为过渡？
5. **是否允许在本机安装/使用隔离 Python venv 与 Node 构建工具**（用于跑测试），不写入仓库。

---

## 7. 建议的阶段顺序（待确认）

- **Block A（Phase 1）**：`actions.json` + `protocol_v2.md` + 校验测试。无破坏性。
- **Block B（Phase 2 基础）**：Alembic、鉴权（auth/app_tokens/device token 哈希）、配置与错误码。
- **Block C（Phase 2 核心）**：`ActionCatalog`、`ActionOrchestrator`、DeepSeek 工具循环、`/api/actions/catalog`。
- **Block D（Phase 2 通道）**：`DeviceGateway`（v1/v2 协议、队列/仲裁）、`AppGateway`（`/app/ws`）。
- **Block E（Phase 3）**：`device_simulator` v2 + `app_simulator` + 全链路测试。
- **Block F（Phase 6）**：Nginx/systemd/部署/回滚 + 手册。
- **Block G（可选，Phase 2.5）**：音频链路（ASR/TTS/Opus/状态机）——如果要求真机语音对话。
- **Block H（独立立项）**：`companion/app` Android App。
- **Block I（独立立项）**：`companion/firmware` ESP32 固件。
