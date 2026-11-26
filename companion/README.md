# RikkaHub Companion Server（伴侣服务端 v2）

RikkaHub 的独立轻量化 Linux 服务端，作为角色、记忆、会话与设备状态的唯一权威数据源。

当前仓库只交付**服务端**；Android App 与 ESP32 固件不在本仓库内（后续独立立项）。原 `companion/web`
管理端已移除，所有管理/遥控能力由 App 通过 REST + WebSocket 使用。

## 架构特性

- **数据无损导入**：解析手机端 RikkaHub 备份 ZIP（`settings.json` 与 `rikka_hub.db`），还原角色人设、
  多分支对话树（`MessageNode`）与长期记忆。
- **轻量稳定**：FastAPI + SQLite 异步 WAL、单进程写入、繁忙锁保护，适配 2 核 2G 轻量服务器。
- **对话与思考链**：DeepSeek 流式 API，支持 ` thinking` 思考过程折叠、自动联网搜索及 SSRF 防护。
- **统一鉴权**：管理员登录签发 App Bearer Token（只存 SHA-256 哈希），除 `/api/health`、
  `/api/auth/login`、`/api/devices/pair` 外全部接口需要鉴权。
- **设备配对**：一次性 6 位配对码（限时、限次、限速），设备用配对码换取独立设备 Token；
  设备 Token 与 App Token 分表存储，互不通用。
- **动作契约**：`companion/shared/actions.json` 是 Server / App / 固件共享的 18 动作单一事实源，
  通过 `GET /api/actions/catalog` 下发。
- **动作编排**：设备在线且角色开启动作联动时，向 DeepSeek 注入 5 个 `robot.*` 工具；单轮最多 3 次动作，
  每设备串行排队（上限 8）、10 条/秒限速、音量/舵机后写覆盖、急停清空队列；执行结果以 `tool_result`
  回传对话流。设备离线或未绑定时不注入工具，只给模型一句状态提示。
- **WebSocket 网关**：`/device/ws` 负责设备握手、指令关联与状态上报落库；`/app/ws` 负责 App 遥控
  （与 LLM 共用同一设备队列）和设备事件广播。Token 撤销会立即断开对应 App 连接。
- **可逆迁移**：Alembic 管理数据库结构，v1 存量库可平滑升级（设备明文 Token 会转为哈希）。

## 启动与运行

```bash
cd companion/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export ADMIN_PASSWORD='请替换为强口令'
export DEEPSEEK_API_KEY='...'
PYTHONPATH=../.. python3 -m companion.server.app.main
```

默认监听 `127.0.0.1:8000`。启动时会自动执行 `alembic upgrade head`（`AUTO_MIGRATE=false` 可关闭）。

> 必须用 `python -m companion.server.app.main` 启动：优雅停机依赖 `CompanionServer`
> 在 uvicorn 关闭连接之前广播设备下线。直接 `uvicorn companion.server.app.main:app`
> 仍可正常服务，但 App 在停机时收不到 `server_shutdown`（只会看到 1012）。
> 多 worker（`workers>1`）不受支持，启动会直接失败。
> 停机有界：应用层 10s、App 通道收尾 2s、1012 之后的连接 drain 20s、lifespan 兜底
> 2s + 2s；超时后剩余连接由 uvicorn 直接断开。

健康检查：`GET /api/health`（公开）。

## 鉴权流程

```bash
# 1. 登录换取 App Token（仅此一次返回明文）
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"你的口令","device_name":"pixel"}'

# 2. 携带 Token 访问业务接口
curl http://127.0.0.1:8000/api/devices \
  -H "Authorization: Bearer <token>"
```

口令可用明文 `ADMIN_PASSWORD`（首次引导）或 `ADMIN_PASSWORD_HASH`（推荐）：

```bash
python -c "from companion.server.app.security.password import hash_password; print(hash_password('你的口令'))"
```

## 主要接口

| 方法 | 路径 | 鉴权 | 说明 |
| :--- | :--- | :--- | :--- |
| GET | `/api/health` | 公开 | 健康检查 |
| POST | `/api/auth/login` | 公开 | 管理员登录签发 Token |
| POST | `/api/auth/logout` | App | 撤销当前 Token |
| GET | `/api/auth/tokens` | App | Token 列表（不含明文/哈希） |
| POST | `/api/auth/tokens/{id}/revoke` | App | 撤销指定 Token |
| POST | `/api/backup/preview` / `confirm` | App | 备份导入 |
| GET | `/api/backup/export` | App | 导出 RikkaHub 兼容 ZIP |
| GET/POST/PUT/DELETE | `/api/assistants`、`/api/conversations`、`/api/memories` | App | 角色/会话/记忆 |
| POST | `/api/chat/completions` | App | SSE 流式对话 |
| GET | `/api/actions/catalog` | App | 动作库 |
| POST | `/api/devices/pair-code` | App | 生成一次性配对码 |
| POST | `/api/devices/pair` | 公开（凭配对码） | 设备换取设备 Token |
| GET | `/api/devices` | App | 设备列表/状态 |
| PUT | `/api/devices/{id}`、`PUT /api/devices/bind` | App | 重命名/绑定 |
| DELETE | `/api/devices/{id}` | App | 撤销设备（软删除，旧 Token 立即失效） |

设备/App WebSocket 均已实现：`/device/ws` 见 `app/services/device_gateway.py`，
`/app/ws` 见 `app/services/app_gateway.py`；音频帧链路属于后续 Block。动作编排已接入对话 SSE
（事件见 `companion/docs/protocol_v2.md` 8.3）。

## 数据库迁移

```bash
cd companion/server
PYTHONPATH=../.. .venv/bin/alembic -c alembic.ini upgrade head
PYTHONPATH=../.. .venv/bin/alembic -c alembic.ini downgrade -1   # 回退一步
```

注意：`0002` 把设备明文 Token 哈希化，downgrade 不可还原明文，回退后设备需重新配对。

## 测试

```bash
# 在仓库根目录执行
python -m pytest -q
python -m companion.shared.validate_actions
```

## 部署

见 `deploy/README`（如无则直接阅读脚本）：

```bash
sudo bash companion/deploy/deploy.sh
```

- `deploy/nginx.conf`：只反代 `/api/`、`/device/ws`、`/app/ws`，其余路径 404。
- `deploy/companion.service`：以非 root 用户 `rikkahub` 运行，单 worker。
- `deploy/companion.env.example`：环境变量模板（不要提交真实密钥）。
