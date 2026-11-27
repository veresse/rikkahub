# Phase 4D 设计：设置 / 素材包 / 备份

> 目标：把 4.3 表第 4 页（备份导入）与第 7 页（设置与素材包）落成 App 里真正可用的纵向切片。
> 本文件只定设计与边界，实施步骤见同目录 `2026-11-27-companion-app-phase4d.md`。
> 全程不读取、不打印、不落日志：API Key、备份正文、Wi-Fi/管理员密码、私聊内容。

## 1. 范围

| 块 | 内容 | 验收 |
| :--- | :--- | :--- |
| 设置 | DeepSeek / 阿里云 ASR & TTS / 搜索适配器入口；API Key **只写掩码**、服务端加密落库、绝不回显；会话/Token 管理 | 写入后下一轮对话/语音立即生效，无需重启；`GET` 只返回“已配置/未配置 + 来源” |
| 素材包 | 上传 ZIP、校验 `240x240` / 帧数 / SHA-256、当前版本与上一版、坏包回退占位图、首帧预览 | 坏包被拒且不影响当前生效包；回滚可用；固件消费留到 Phase 5 |
| 备份 | SAF 选 ZIP → `/backup/preview` → 确认 → `/backup/confirm` → 结果；另外提供导出 ZIP | 上传不落盘（流式）、预览过期有明确提示、导出流式写 SAF |

不做：小米健康、主动问候、4G、摄像头、世界书、正则、逐字口型、第三轴、AEC、OTA、应用商店；
素材包固件下载/热替换协议（Phase 5）、管理员密码修改、搜索 Key（当前内置适配器不需要）。

## 2. 服务端

### 2.1 设置存储

新表 `app_settings`（迁移 `0004_settings_assets`）：

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| `key` | String(64) PK | 白名单键名 |
| `value` | Text | 明文键直接存；密钥列存 `v1:` + base64(nonce‖ciphertext) |
| `is_secret` | Boolean | 密钥列标记 |
| `updated_at` | Integer(ms) | 审计用，不记录旧值 |

白名单（未知键直接 400）：

- 密钥：`deepseek_api_key`、`dashscope_api_key`
- 普通：`deepseek_base_url`、`deepseek_model`、`voice_enabled`、`voice_provider`、
  `dashscope_region`、`dashscope_asr_model`、`dashscope_tts_model`、`dashscope_tts_voice`、
  `search_enabled`

### 2.2 密钥保护

- 新增依赖 `cryptography`，用 AES-GCM（12 字节随机 nonce）。
- 密钥由 `HKDF-SHA256(ikm=SECRET_KEY, salt=b"companion-settings-v1", info=b"aes-gcm")` 派生，
  `SECRET_KEY` 本身已在 `DATA_DIR/secret.key`（0600）。
- 解密失败（`SECRET_KEY` 轮换/密文损坏）：**不崩溃、不回显**，按未配置处理并在视图里带
  `error="decrypt_failed"`，日志只记键名。
- 备份导出**不包含** `app_settings`（导出逻辑只读角色/会话/节点/记忆）。

### 2.3 运行时生效

`services/app_settings.py` 提供单例服务：

- `load_and_apply(db)`：启动 `lifespan` 里 `init_db()` 之后调用，把 DB 覆盖值写回
  `settings.<FIELD>`（pydantic-settings 实例允许赋值）。
- `set_plain / set_secret / clear_secret`：写库后立即 `apply`，下一轮对话/语音即生效。
  现有消费方（`DeepSeekClient.__init__`、`build_voice_provider`、`SearchProvider` 调用点）
  都是调用时读 `settings`，无需改构造函数。
- 语音“有效提供方”推导：`VOICE_ENABLED=false` → `unavailable`；
  `voice_provider=dashscope` 但无 Key → `unavailable` + `reason=dashscope_key_missing`。
- 搜索总开关：`config.SEARCH_ENABLED: bool = True`，`orchestrator` 取搜索结果前判断；
  关闭时跳过搜索、其余流程不变。

### 2.4 设置 API（全部需要 App Token）

`GET /api/settings`：

```json
{
  "deepseek":  {"configured": true, "source": "database", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
  "dashscope": {"configured": false, "source": "unset", "region": "cn-beijing",
                "asr_model": "fun-asr-realtime", "tts_model": "qwen-audio-3.0-tts-flash", "tts_voice": "cherry"},
  "voice":     {"enabled": true, "provider": "unavailable", "effective": "unavailable", "reason": "dashscope_key_missing"},
  "search":    {"enabled": true, "provider": "builtin_bing", "requires_key": false}
}
```

`source` ∈ `database | environment | unset`（只看“有效值从哪来”，不含任何明文或掩码片段）。

- `PUT /api/settings`：普通配置全字段可选；`deepseek_base_url` 仅允许 `http/https`、无
  userinfo、≤200 字符；`voice_provider` ∈ `{unavailable, dashscope}`；字符串字段去空白后
  非空、长度受限。返回与 `GET` 相同的视图。
- `PUT /api/settings/secrets`：`{ "deepseek_api_key": "...", "dashscope_api_key": "..." }`，
  字段可选；`""` 表示清除；长度 1–512。**只入不出**，响应仍是 `GET` 视图。
- `DELETE /api/settings/secrets/{key}`：白名单键才允许，等价于写空。

### 2.5 素材包规范（最小可用）

包结构（只允许这些条目）：

```
manifest.json
frames/000.png
frames/001.png
...
```

`manifest.json`：

```json
{
  "name": "Q版陪伴",
  "version": "1.0.0",
  "fps": 12,
  "frames": ["frames/000.png", "frames/001.png"]
}
```

校验（任一失败 → 400，附 `detail` 原因码）：

- ZIP ≤ 10 MiB、解压总量 ≤ 20 MiB（新配置 `ASSET_MAX_*`），条目数 ≤ 256，帧数 1–240；
- 拒绝 zip-slip / 绝对路径 / 反斜杠 / 符号链接 / 重复条目 / 大小写冲突 / 额外文件；
- `manifest.json` 必须在根、≤ 16 KiB、UTF-8、JSON 对象；`version` 1–32、`fps` 1–60；
- 帧必须是 PNG（签名 + IHDR：宽高都是 240、bit depth 8、color type ∈ {2,6}），单帧 ≤ 512 KiB；
- `frames` 顺序即播放顺序；每帧算 SHA-256 写入存储清单，整包 SHA-256 =
  `sha256(按顺序 "路径:hash\n" 拼接)`，供 Phase 5 固件完整性校验。

存储：`DATA_DIR/assets/<package_id>/`（清单 + 帧），DB 表 `asset_packages`
（id / name / version / fps / frame_count / size_bytes / sha256 / storage_path / state /
created_at / activated_at）。`state` ∈ `active | previous`，同一时刻最多各一条，由模块级
`asyncio.Lock` + 事务保证（服务端按单进程部署，多 worker 不在支持范围）。

占位图：仓库内置 `companion/server/app/assets/placeholder_240.png`（240×240），
坏包、无包、回滚到底时使用。

### 2.6 素材包 API（全部需要 App Token）

- `GET /api/assets` → `{ "active": {...}|null, "previous": {...}|null, "fallback": {"name":"占位图","version":"builtin","frame_count":1} }`
- `POST /api/assets/preview`（multipart ZIP）→ 校验后暂存（内存索引 + 临时目录，10 分钟
  TTL、最多 4 个，按 owner token 隔离）：
  `{ "upload_id", "name", "version", "fps", "frame_count", "size_bytes", "sha256" }`
- `POST /api/assets/confirm` `{ "upload_id" }` → 旧 `active` 降为 `previous`、旧 `previous`
  删除（行 + 目录）、新包生效；返回 `GET` 同款视图。
- `POST /api/assets/rollback` → 删除 `active`、`previous` 升为 `active`；没有上一版时 409
  `no_previous_package`。
- `GET /api/assets/{package_id}/frame/{index}` → `image/png` 字节（`index` 为整数，
  越界 404，路径不拼接用户输入）；`package_id=fallback` 返回内置占位图。
- 暂存区在每次 preview/confirm 时清理过期项；服务重启即丢弃（未确认的包不生效）。

### 2.7 备份接口加固

- `POST /api/backup/preview`：复制上传流时按 `MAX_UPLOAD_SIZE_BYTES` 计数，超限 413
  `file_too_large`；预览缓存加 owner token、10 分钟 TTL、最多 8 条，每次访问清理过期项。
- `POST /api/backup/confirm`：owner 不匹配 / 过期 → 400 `preview_expired`；行为不变
  （单事务落库）。
- `GET /api/backup/export`：文件名带日期；用 `BackgroundTask` 删除临时目录（现在会泄漏）。
- 预览只返回计数与样例标题；**不记录、不打印**任何正文。

## 3. App

### 3.1 路由与入口

`AppRoute` 追加（只追加，不插入）：

```kotlin
data object Settings : AppRoute
data object Assets : AppRoute
data object Backup : AppRoute
```

- `AppRouteSaver` 同步支持。
- 角色列表顶栏改为 `设备 / 设置 / 刷新`；`退出登录` 移到设置页（带确认），避免四个按钮。
- 设置页是枢纽：模型服务、语音服务、联网搜索、素材包（→ Assets）、备份（→ Backup）、
  会话/Token、退出登录。
- 每个路由仍独立 `ViewModelStore`；离开页面即销毁 VM（取消上传）。

### 3.2 设置页

- 三段式设置组（大圆角 `Surface`，与现有卡片风格一致），顶部离线横幅复用现有写法。
- DeepSeek：状态行（已配置/未配置 + 来源）、`base_url`、`model` 文本框、Key 输入框
  （`PasswordVisualTransformation`，占位“只写，不回显”）、`保存` / `清除 Key`（确认框）。
- 阿里云百炼：语音开关（关闭/百炼）、region、ASR/TTS 模型、TTS 音色、Key 输入框 + 保存/清除；
  状态行显示“有效提供方”，未配 Key 时提示 `dashscope_key_missing` 的人话版本。
- 联网搜索：内置 Bing 摘要（无需 Key）+ 总开关。
- 会话/Token：只列未撤销且未过期的 Token（设备名、创建时间、最近使用、“当前”标记）；
  撤销需确认；撤销当前 Token 成功后直接 `sessionManager.clear()` 回登录页。
- **Key 只保存在 ViewModel 内存**：输入放 VM 的 `*KeyDraft`（绝不用 `rememberSaveable`），
  保存成功后立即清空；任何日志、异常文本、事件文案都不得包含输入值。
- 写操作串行化：`saving` 时禁用保存/清除，返回键在保存中拦截。

### 3.3 素材包页

- 当前包卡片：名称/版本/fps/帧数/大小/启用时间 + 首帧预览 + `回滚到上一版`（确认框）。
- 上传：SAF `OpenDocument("application/zip")` → 流式上传 → 预览摘要 → `启用此素材包`。
- 首帧预览：`GET .../frame/0` 取 ≤1 MiB 字节，Screen 里 `BitmapFactory` 解码
  （单测只断言字节，不碰 Android Bitmap）。
- 上传/确认在途时拦截返回；失败显示原因码对应文案。

### 3.4 备份页

- `选择备份文件` → 流式上传预览（不写 App 缓存，不保留字节）→ 摘要卡（角色/会话/节点/
  记忆数 + 前 5 个名称/标题）→ `确认导入` → 结果卡（导入计数），并提示“返回角色列表后自动刷新”。
- `导出服务器备份` → `CreateDocument("application/zip")` → 直接把响应流写入 SAF，
  完成后提示大小；不整包进内存。
- 预览 10 分钟过期：失败提示“预览已过期，请重新选择文件”，清空旧预览。
- 上传/导入/导出在途时拦截返回；离线时按钮禁用。

### 3.5 网络与仓库

新增 `CompanionApi` 声明 + 四个仓库（沿用 `SessionApi.call {}` 与 `AppResult`）：

- `SettingsRepository`：`get / update / updateSecrets / clearSecret`
- `AssetRepository`：`get / preview(zipStream) / confirm / rollback / frameBytes`
- `BackupRepository`：`preview(zipStream) / confirm / exportTo(OutputStream)`
- `TokenRepository`：`list / revoke`

上传用自定义 `InputStreamRequestBody`（`isOneShot=true`、`contentLength=-1`，流式 chunked），
文件名固定 `backup.zip` / `assets.zip`；导出 `@Streaming` + 8 KiB 缓冲循环写目标流。

DTO 全部 `@Serializable` + `@SerialName` snake_case，字段带默认值，服务端加字段不炸旧客户端。

### 3.6 离线与在途

- 所有写按钮受 `isOnline` 门控；设置页只读展示可在离线查看（数据来自上次加载）。
- 上传/导入/导出/保存/撤销各自 `busy`，在途拦截返回（与 4A–4C 一致）。
- 素材/备份页面加载失败显示可重试错误，不把异常直接抛给 UI。

## 4. 测试

服务端（pytest，新增 `test_settings_api.py`、`test_assets_api.py`、`test_backup_api.py`）：

1. 设置：默认视图、普通字段写后生效于 `settings`、密钥不回显、DB 原始值不含明文、
   清除、未知键 400、base_url 非法 400、解密失败降级、启动加载覆盖。
2. 搜索开关：`SEARCH_ENABLED=false` 时编排不调用搜索。
3. 素材：合法包 preview/confirm → active + 帧可读；尺寸错/帧数超限/zip-slip/额外文件/
   重复条目/伪 PNG 全部 400 且现有 active 不变；rollback；staged TTL；owner 不匹配。
4. 备份：preview owner 隔离、TTL 过期、超大文件 413、导出可被 `validate_and_extract_backup_zip`
   + `BackupParser` 重新解析（round-trip）、导出临时目录被清理。
5. 迁移：`alembic upgrade head` 后新表存在（沿用 `test_migrations.py` 风格）。

App（JVM 单测，新增 4 个 VM 测试 + 路由 saver 用例）：

1. `SettingsViewModel`：加载映射；保存只发普通字段；密钥保存成功后输入清空且不进入状态；
   离线拦截；清除密钥；撤销当前 Token 触发本地清会话。
2. `AssetsViewModel`：preview → confirm → active 摘要；错误码文案；rollback；帧字节上限。
3. `BackupViewModel`：preview → confirm → 结果；过期提示清空预览；导出写入给定 `OutputStream`；
   在途 `busy` 阻止重复提交。
4. `AppRouteBackStackSaver`：新路由可保存/恢复，旧路由位置不变。

验收命令：`gradlew testDebugUnitTest assembleDebug assembleRelease` + `pytest`，
App 基线 223 例、服务端 427 例只增不减。

## 5. 风险与取舍

- **新增依赖 `cryptography`**：换取密钥不裸存 DB；若不允许加依赖，退化为明文存
  `app_settings`（风险更高，不推荐）。轮换 `SECRET_KEY` 会丢失已存密钥，视图给出
  “需重新填写”，不会静默失效。
- **改 `settings` 单例**：是全局可变状态，测试需在 fixture 里快照/恢复；生产只有设置服务写它。
- **素材包只做 App 管理侧**：设备端下载/热替换属于 Phase 5，本轮只保证“包正确、可回滚、
  有占位图”。
- **备份导入是合并语义**（按 id 更新/插入），不删服务器上多出来的数据；页面明确提示。
- **测试库沿用开发库**：新表测试结束要清空 `app_settings` / `asset_packages` 行与临时目录，
  避免污染本地数据。

## 6. 交付日志

实施与审查记录写入 `docs/语音机器人需求文档/v2_migration_log/31_phase4_4d_settings_assets_backup.md`，
并在索引 `README.md` 追加一行。
