# 03 · Block B（Phase 2 基础）：鉴权、Alembic 迁移与移除网页端

- 日期：2026-11-25
- 状态：✅ 完成
- 分支：`feature/companion-robot-v2`
- 前置决策（负责人确认）：
  1. 本轮只做**服务端**；Android App 与 ESP32 固件暂不做。
  2. 先做**控制面**，音频链路后续单独 Block。
  3. 删除 `companion/web`，App 后续再考虑。
  4. 鉴权直接作用于现有 REST，不留匿名过渡期。

## 1. 目标与范围

1. 新增管理员登录与 App Bearer Token（哈希存储、可撤销、可过期）。
2. 设备 Token 改为哈希存储，配对码落库并支持一次性兑换、限时、限次、限速。
3. 引入 Alembic 可逆迁移，保证 v1 存量库平滑升级。
4. 移除 `companion/web` 与 FastAPI 静态挂载，服务端只提供 REST/WS。
5. 所有业务接口默认要求 App Token；仅健康检查、登录、设备配对公开。

## 2. 改动文件清单

### 新增

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/security/__init__.py` | 安全工具导出 |
| `companion/server/app/security/password.py` | PBKDF2-HMAC-SHA256 口令哈希/校验 |
| `companion/server/app/security/tokens.py` | Token 生成与 SHA-256 哈希 |
| `companion/server/app/security/rate_limit.py` | 进程内滑动窗口限速 |
| `companion/server/app/security/dependencies.py` | `require_app_token` Bearer 鉴权依赖 |
| `companion/server/app/models/app_token.py` | App Token 表 |
| `companion/server/app/models/pairing_code.py` | 配对码表 |
| `companion/server/app/routers/auth.py` | 登录/登出/Token 列表/撤销 |
| `companion/server/app/routers/actions.py` | `GET /api/actions/catalog` |
| `companion/server/alembic.ini` | Alembic 配置 |
| `companion/server/migrations/env.py` | 迁移运行环境 |
| `companion/server/migrations/script.py.mako` | 迁移模板 |
| `companion/server/migrations/versions/0001_v1_baseline.py` | v1 基线表 |
| `companion/server/migrations/versions/0002_v2_actions_auth.py` | v2 设备字段、Token、配对码、动作开关 |
| `companion/server/tests/conftest.py` | ASGI client、登录 fixture、限速重置 |
| `companion/server/tests/test_auth.py` | 鉴权/限速/撤销测试 |
| `companion/server/tests/test_devices.py` | 配对/设备管理/Token 隔离测试 |
| `companion/server/tests/test_migrations.py` | 迁移双向与数据保留测试 |
| `companion/deploy/companion.env.example` | 环境变量模板（无真实密钥） |

### 修改

| 路径 | 说明 |
| :--- | :--- |
| `companion/server/app/config.py` | 持久化 `SECRET_KEY`、管理员口令、Token/配对/限速配置、CORS 白名单、`AUTO_MIGRATE` |
| `companion/server/app/database.py` | 启动走 Alembic；保留 WAL/外键/繁忙超时 |
| `companion/server/app/main.py` | 移除静态挂载；注册 auth/actions；业务路由统一挂鉴权 |
| `companion/server/app/models/device.py` | Token 哈希、协议版本、能力、音量、偏移、状态、最近上报 |
| `companion/server/app/models/assistant.py` | 新增 `enable_action_sync` |
| `companion/server/app/models/__init__.py` | 导出新模型 |
| `companion/server/app/routers/devices.py` | 重写配对/设备管理 |
| `companion/server/tests/test_full_system.py` | 加登录、移除静态页断言 |
| `companion/deploy/nginx.conf` | 只反代 `/api/`、`/device/ws`、`/app/ws`，其余 404 |
| `companion/deploy/deploy.sh` | 非 root 用户、数据目录、环境文件、迁移前备份 |
| `companion/deploy/companion.service` | 非 root、EnvironmentFile、安全加固 |
| `companion/README.md` | 更新为 v2 服务端说明 |
| `pytest.ini` | `asyncio_mode = auto` |
| `conftest.py` | 测试环境默认 `ADMIN_PASSWORD` 与 `AUTO_MIGRATE=true` |

### 删除

| 路径 | 说明 |
| :--- | :--- |
| `companion/web/**`（含 `dist`） | 按负责人决定移除网页管理端 |

## 3. 关键实现说明

### 3.1 鉴权

- `Authorization: Bearer <app_token>`，禁止 Query 传 Token。
- App Token 表只存 SHA-256；签发时返回一次明文。
- Token 过期、撤销、设备 Token 冒用统一返回 `401 {"detail": "unauthorized"}`。
- 登录失败按 `IP + 用户名` 滑动窗口限速（默认 5 次 / 300 秒）；超限返回 `429 {"detail": "rate_limited"}`。
- 未配置管理员口令返回 `503 admin_password_not_configured`，避免“空口令即通过”。
- `SECRET_KEY` 默认持久化到 `DATA_DIR/secret.key`，不再每次启动随机。

### 3.2 配对与设备

- 配对码 6 位、哈希落库、一次性、默认 300 秒有效。
- 同一 App Token 每分钟最多生成 5 个配对码。
- 同一 IP 连续 5 次错误配对尝试后，后续尝试统一 `400 invalid_pair_code`（不区分码不存在/错误）。
- 兑换成功返回一次 `device_token`（64 hex）；设备表只存哈希。
- 撤销设备为软删除：`is_active=false`、随机替换 `token_hash`、`state=revoked`，旧 Token 立即失效。

### 3.3 迁移

- `0001_v1_baseline`：v1 五张表（角色/会话/分支/记忆/设备）。
- `0002_v2_actions_auth`：
  - `assistants.enable_action_sync`；
  - `devices` 明文 `token` → `token_hash`（升级时逐行 SHA-256）；
  - 新增协议版本、固件版本、能力、音量、待机开关、校准偏移、状态、最近上报；
  - 新增 `app_tokens`、`pairing_codes`。
- downgrade 能恢复表结构；但哈希不可逆，回退后设备需重新配对（已在 README 说明）。

## 4. 实际执行的命令与真实输出

### 4.1 首次运行（失败）

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
...
E   UnicodeDecodeError: 'gbk' codec can't decode byte 0xae in position 26: illegal multibyte sequence
30 passed, 34 errors in 10.69s
```

原因：Windows 默认 GBK 读取 `alembic.ini`，而文件含中文注释。已将 `alembic.ini` 改为纯 ASCII 并补
`path_separator = os`。

### 4.2 修复后全量测试

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
................................................................         [100%]
64 passed in 3.89s
```

### 4.3 动作库校验

```text
$ & "...\companion-venv\Scripts\python.exe" -m companion.shared.validate_actions
OK: actions=18 version=2.0.0 default_max=3000ms hard_max=5000ms
```

### 4.4 删除网页端

```text
$ git rm -r -q companion/web
$ Test-Path companion/web
False
```

## 5. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| 错误响应暂用 `{"detail": "<code>"}`，未同时附 `message` | 现有 v1 客户端/测试都读取 `detail`；先保持兼容，协议里的 `message` 字段在 App 接入时统一补 | 与 `protocol_v2.md` 第 11.6 条不完全一致，后续需一次性迁移 |
| 配对失败按 IP 限次，而非按配对码记录递增 | 错误码无法反查记录（码是哈希），按 IP 才能阻止暴力枚举 | 同一 NAT 下多设备会共享限次；家庭场景可接受 |
| `DELETE /api/devices/{id}` 改为软删除 | 保留历史与审计；同时立即失效旧 Token | 设备列表仍会显示已撤销设备（`is_active=false`） |
| App Token 使用不透明随机串而非 JWT | 可即时撤销、只存哈希；无需引入签名密钥轮换 | 每次请求多一次数据库查询（已按 60 秒节流更新 `last_used_at`） |
| `pytest.ini` 开启 `asyncio_mode = auto` | 新增测试直接写 `async def`，减少样板 | 对既有测试无影响（64 passed 验证） |

## 6. 未完成项 / 阻塞点

- `/device/ws`、`/app/ws` 尚未实现（Block D）。
- 动作编排、DeepSeek 工具调用循环尚未实现（Block C）。
- 设备在线状态目前仅来自数据库字段，尚无实时网关。
- 音频链路（ASR/TTS/Opus/状态机）未实现（后续独立 Block）。
- Android App 与 ESP32 固件不在本轮范围。

## 7. 下一步

进入 **Block C（Phase 2 核心）：ActionCatalog / ActionOrchestrator / DeepSeek 工具调用聚合与执行 /
`/api/actions/catalog` 联调**。随后 Block D 实现 `DeviceGateway` 与 `AppGateway`。
