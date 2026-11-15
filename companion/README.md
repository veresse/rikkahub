# RikkaHub Companion Server (伴侣服务端)

为 RikkaHub 打造的独立轻量化 Linux 服务端与 Web 管理系统，作为角色、记忆与会话的唯一权威数据源，同时为实体语音挂件提供核心编排支持。

## 架构特性
- **数据无损导入**：完整解析手机端 RikkaHub 备份 ZIP 包（`settings.json` 与 `rikka_hub.db`），还原角色人设、多分支对话树（`MessageNode`）与长期记忆。
- **轻量稳定**：基于 Python FastAPI + SQLite 异步 WAL 模式，单进程写入与繁忙锁保护，完美适配 2核 2G 腾讯云轻量服务器。
- **对话与思考链**：深度接入 DeepSeek 流式 API，支持 `<think>` 思考过程折叠、自动联网搜索及 SSRF 安全防护。
- **分支切换**：完整保留原版手机 App 的分支选择机制，用户可随意在多个候选生成版本之间切换。

## 启动与运行 (Linux/Debian)

```bash
cd companion/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动服务 (默认监听 127.0.0.1:8000)
python3 -m companion.server.app.main
```

## 测试

```bash
# 运行离线导入与解析验证
python3 companion/server/tests/verify_standalone.py
```
