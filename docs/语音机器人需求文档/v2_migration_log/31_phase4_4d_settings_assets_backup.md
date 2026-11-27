# 31 - Phase 4D：设置 / Token、备份导入导出、素材包

- 分支：`feature/companion-robot-v2`
- 计划：`docs/superpowers/plans/2026-11-27-companion-app-phase4d.md`
- 设计：`docs/superpowers/plans/2026-11-27-companion-app-phase4d-design.md`
- 前置提交：`ede1e71e`（4C App 遥控页）
- 本日志覆盖：`0db450b3` 起的 Task 10–14 与两轮审查修复提交

## 1. 目标与范围

把运行时可改的设置、App Token 管理、备份导入/导出、素材包管理接到已有的 App 上，
并保持“服务端不回显任何 Key / 备份正文 / 素材包内容”的约束。

范围内：

- 服务端设置 KV（`app_settings`）与 AES-GCM 加密密钥；`GET/PUT /api/settings`、
  `PUT /api/settings/secrets`、`DELETE /api/settings/secrets/{key}`。
- `SEARCH_ENABLED` 运行时开关与 orchestrator 门控。
- App Token 列表只显示仍有效的登录；重复撤销返回 404。
- 备份预览 / 确认导入 / 导出；上传体积早退、预览 TTL/owner/容量、导出临时目录清理。
- 素材包 ZIP 校验（manifest + 240×240 PNG 序列）、暂存 / 启用 / 回滚 / 按索引取帧、
  内置占位图；App 素材页上传 / 确认 / 回滚 / 首帧预览。
- App 新路由 `Settings`、`Backup`、`Assets`（只追加，不改变既有路由的保存顺序）。

不在范围（与 4D 设计一致）：设备端素材包下载与热替换（Phase 5）、主动问候、摄像头、
AEC 全双工、不安全 OTA、应用商店分发。

## 2. 改动文件

### 2.1 服务端（`companion/server`）

新增：

- `app/models/asset_package.py`
- `migrations/versions/0005_asset_packages.py`
- `app/assetpacks/__init__.py`
- `app/assetpacks/validator.py`
- `app/assetpacks/service.py`
- `app/assetpacks/placeholder_240.png`（240×240 RGB，527 字节）
- `app/routers/assets.py`
- `app/services/upload_limits.py`
- `tests/test_asset_packs.py`
- `tests/test_assets_api.py`
- `tests/test_upload_limits.py`
- `tests/test_voice_runtime_swap.py`

修改：

- `app/config.py`：`ASSET_MAX_ARCHIVE_BYTES=10 MiB`、`ASSET_MAX_EXTRACTED_BYTES=20 MiB`、
  `ASSET_MAX_FRAMES=240`、`ASSET_MAX_FRAME_BYTES=512 KiB`。
- `app/main.py`：注册 assets 路由；注册 `ContentLengthLimitMiddleware`。
- `app/routers/auth.py`：Token 列表过滤撤销/过期；重复撤销 404 `token_not_found`。
- `app/routers/import_export.py`：503/413/预览错误码、导出句柄与临时目录清理。
- `app/routers/settings.py`：语音运行时重建失败只记录日志。
- `app/services/app_settings.py`：解密失败的行不再谎报 `source=database`。
- `app/services/device_gateway.py`：运行时对象变化时关闭旧 `VoiceSession`。
- `app/importer/service.py`：预览容量按插入顺序裁剪；导入失败保留预览可重试。
- `tests/{test_app_settings,test_auth,test_backup_api,test_importer,test_settings_api,test_migrations}.py`。

### 2.2 App（`companion/app`）

新增：

- `data/api/dto/AssetDtos.kt`
- `data/repo/AssetRepository.kt`
- `feature/assets/AssetsViewModel.kt`
- `feature/assets/AssetsScreen.kt`
- `testutil/FakeAssetRepository.kt`
- `data/repo/AssetRepositoryTest.kt`
- `feature/assets/AssetsViewModelTest.kt`
- `feature/assets/AssetsScreenGateTest.kt`

修改：

- `data/api/CompanionApi.kt`：素材包 5 个端点（预览 multipart、确认、回滚、取帧 `@Streaming`）。
- `data/api/ErrorMapper.kt`：素材包校验码、`token_not_found` 文案。
- `core/AppContainer.kt`：`assetRepository`。
- `ui/navigation/{AppRoute,AppRouteSaver,NavigationViewModel,AppNav}.kt`：`Assets` 路由、
  保存顺序追加、`viewModelKey()="assets"`、在途禁止返回。
- `feature/settings/{SettingsScreen,SettingsViewModel}.kt`：素材入口、Token 过滤、事件不再 replay。
- `tests/.../{NavigationViewModelTest,SettingsViewModelTest}.kt`。

## 3. 实际执行的命令与输出

服务端（`companion/server`，本机缺 `aiosqlite`，统一用 `uv run`）：

```powershell
$env:PYTHONPATH="..\.."
uv run --python 3.12 --with-requirements requirements.txt python -m pytest -q
```

关键阶段输出：

- Task 10：`tests/test_asset_packs.py tests/test_migrations.py` → **13 passed**
- Task 11：`tests/test_assets_api.py tests/test_asset_packs.py` → **18 passed**
- Task 10/11 全量：**477 passed / 1 failed**
- Task 3–6 审查修复后全量：**491 passed / 1 failed**
- 最终审查（Task 7–15）修复后全量：**502 passed / 1 failed in 19.13s**
- 最终复核（第三轮 P3）修复后全量：**503 passed / 1 failed in 18.97s**
- 第四轮复核修复后全量：**503 passed / 1 failed in 18.53s**
- 第五轮复核修复后全量：**504 passed / 1 failed in 18.26s**
- 第六轮复核修复后全量：**504 passed / 1 failed in 18.28s**
- 第七轮（收尾）修复后全量：**506 passed / 1 failed in 18.79s**（新增 2 个失败路径用例）

唯一失败为既有环境/依赖漂移：

```text
FAILED tests/test_audio_pipeline.py::test_frames_are_dropped_during_thinking_and_speaking
AssertionError: condition not met before timeout
```

该失败在干净 HEAD（`git stash push -u` 后）同样出现，非 4D 引入；未修改该用例。

App（`companion/app`）：

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug assembleRelease --console=plain
```

输出：

```text
BUILD SUCCESSFUL
94 actionable tasks: 15 executed, 79 up-to-date
```

测试汇总（`build/test-results/testDebugUnitTest/TEST-*.xml`）：

```text
suites=34 tests=278 failures=0
```

专项：

- `AssetRepositoryTest`：7 passed
- `AssetsViewModelTest`：14 passed
- `AssetsScreenGateTest`：1 passed
- `NavigationViewModelTest`：6 passed
- `SettingsViewModelTest`：12 passed

## 4. 偏差与 Ruling

1. **迁移拆分**：设计中的单个 `0004_settings_assets` 拆成 `0004_app_settings` 与
   `0005_asset_packages`，让两个子块可以独立审查/回退；`test_migrations.py` 按 revision
   显式降级，避免 `head-1` 漂移。
2. **Key 草稿位置**：草稿留在 `SettingsViewModel` 内存态（成功后清空、失败保留重试），
   不进 `rememberSaveable`；错误文案与事件都不包含输入值。
3. **素材包规范落地**：ZIP 只允许 `manifest.json` + `frames/NNN.png`；校验时把帧重写为
   `frames/{index:03d}.png`（顺序=manifest 顺序）并写 `manifest.json` / `integrity.json`
   （整包 SHA-256 = 按序 `"path:hash\n"`）。`AssetService.frame_bytes(package_id, index, db)`
   多一个 `db` 参数：包 ID 必须经 DB 解析，禁止用请求参数拼路径。
4. **占位图位置**：按计划放在 `app/assetpacks/placeholder_240.png`（设计文字写的是
   `app/assets/`）；由一次性脚本生成，脚本未提交。
5. **上传 413 早退**：FastAPI 会在进入路由前读完 multipart，因此路由内的分块计数只能限制
   内存。新增纯 ASGI `ContentLengthLimitMiddleware`，在读取请求体前按 `Content-Length`
   拒绝明显超限的上传（保留 64 KiB multipart 余量）；分块计数继续作为兜底。
6. **预览缓存语义**：容量按插入顺序淘汰（dict 保序），不能按 `expires_at` 排序——同秒
   创建的预览 TTL 相同，会把刚插入的条目误删。
7. **导入失败语义**：确认导入先“占用”预览、成功后才删除；DB/约束失败时恢复占用并保留
   预览，路由只把守卫自己的 `preview_expired` 映射成该错误码，其它错误回“导入数据失败”。
8. **事件重放**：设置页 snackbar 使用 `replay = 0`，旋转屏幕重建收集器不会重复弹出上一条。
9. **语音热替换**：`DeviceSession` 的 `VoiceSession` 会捕获当轮运行时；仅当运行时变为
   `None` 才关闭不够——对象变化时也关闭旧会话，下一轮按新 Key/模型重建。重建失败只写日志，
   因为设置此时已经 commit。
10. **Token 生命周期**：服务端列表只返回 `revoked_at IS NULL AND expires_at > now`；App
    再用 `visibleTokens()` 兜一层（兼容旧服务端）。重复撤销返回 404，不再假报成功。
11. **素材首帧预览**：App 无 Coil，使用 OkHttp 取字节 + `BitmapFactory.decodeByteArray`；
    JVM 单测只断言字节与 1 MiB 上限，不依赖 Bitmap 解码。

## 4.1 最终审查（Task 7–15）修复

第二轮只读审查报告 2 个 P1、3 个 P2、7 个 P3，全部处理：

1. **P1 流式上传绕过 Content-Length**：App 的 `StreamRequestBody` 没有长度，服务端
   改为包装 ASGI `receive` 按累计字节早退；超过 `limit + 64 KiB` 立即 413。哨兵异常
   必须继承 `BaseException`，否则 FastAPI 会把解析期异常改写成 400。
2. **P1 任意设置保存都会关闭语音会话**：`build_voice_runtime()` 每次都新建对象，
   `set_voice_runtime` 的同一对象判断在生产路径永远不成立。设置路由改为只对
   `VOICE_*`/`DASHSCOPE_*` 做指纹比较，指纹不变就不重建；新增“搜索/DeepSeek 保存
   不关闭已连接会话、语音保存会关闭”的用例。
3. **P2 畸形 ZIP/manifest 变 500**：先校验 `frames` 元素都是字符串再 `set()`；
   `zlib.error`/`OSError` 收敛为 `invalid_archive`。
4. **P2 过期首帧响应串包**：`AssetsViewModel` 增加 generation + 可取消 Job；
   `load`/`confirm`/`rollback` 使在途请求失效，active 变化时清空旧字节；图片成功
   不再清除无关错误。
5. **P2 素材暂存超容量/误删最新**：插入后再按插入顺序淘汰；`preview` 在线程池里
   执行，`_PENDING` 用 `threading.RLock` 串行化。
6. **P3**：`frameBytes` 只读到 1 MiB + 1 字节；取消选择文件不再报错；导入被取消也
   解除 `claimed`；重复撤销 `token_not_found` 时 App 删除死行并刷新；导入/导出只回
   稳定错误码并只记录异常类型；根 `conftest.py` 强制临时 `DATA_DIR` 并在退出时删除；
   `AssetService.preview` 用 `asyncio.to_thread` 避免阻塞音频事件循环。

## 4.2 最终复核（第三轮）修复

第三轮只读复核结论为 **0 P0 / 0 P1 / 0 P2 / 9 P3**，前两轮的 2 P1 + 3 P2 确认真实修复；
P3 中 8 条已处理（P3-8 的 e2e 弱覆盖保留，早退行为由 `test_upload_limits.py` 覆盖）：

1. **临时库目录残留**：`atexit` 删除时 aiosqlite 工作线程仍持有 WAL/SHM，Windows
   静默失败。根 `conftest.py` 增加 `pytest_sessionfinish`，先 `engine.dispose()`
   再删目录；实测 1847 个历史残留目录已清理，新跑一轮不再新增。
2. **事件循环里删素材目录**：`_prune()` 改为锁内只改字典并返回待删目录，`confirm`/
   `rollback` 用 `asyncio.to_thread` 删除；`preview` 在自身线程里删。
3. **他人 `upload_id` 被顺手作废**：`confirm` 对 owner 不匹配只回 `upload_expired`，
   不 pop、不删目录；新增“owner-a 的包仍可被 owner-a 确认”的用例。
4. **语音指纹竞态**：并发 A: x→y、B: y→x 时 B 可能读到旧快照而漏重建；改为
   “写过语音字段就重建”，新增同值写入仍重建的用例（第四轮复核指出该做法会误关
   正在进行的语音通话，已在 §4.3 改为锁内快照比较）。
5. **首帧失败清空已显示图片**：同一包的瞬时失败只更新 error，保留上一张帧；
   新增用例。
6. **刷新与确认竞态**：`load()` 增加 generation，确认/回滚成功后使在途刷新过期并
   复位 `loading`；新增用例在去掉守卫后确实失败（已手工验证）。
7. **弱测试**：`test_pending_cache_caps_and_keeps_newest` 固定时钟到 1.7e9（同秒并列，
   旧实现会淘汰最新一条）；取消/停机时 `os.remove` 捕获 `OSError`，不让清理异常覆盖取消。

## 4.3 第四轮复核修复

第四轮只读复核结论为 **0 P0 / 0 P1 / 0 P2 / 2 P3**，均处理：

1. **原值保存会关闭语音会话**：上一轮“写过语音字段就重建”把 App 每次整组提交
   语音字段的行为变成了“点保存就打断通话”。改为 `AppSettingsService.update()`
   接受 `snapshot` 回调，在写入锁内取 `before`/`after` 并返回；路由只在指纹真正
   变化时重建。这样既消除并发 x→y/y→x 的漏重建，也不会在空写时关闭会话。
   测试改为“同值保存不重建”，原有的“不同值重建”用例保留。
2. **目录删除仍在 `_lock` 内**：`confirm`/`rollback` 改为锁内只收集待删目录并取
   `view()`，退出锁后再 `asyncio.to_thread` 删除，避免第二个管理请求排队等 rmtree。

## 4.4 第五轮复核修复

第五轮只读复核结论为 **0 P0 / 0 P1 / 0 P2 / 2 P3**，均处理：

1. **`confirm` 失败分支仍持锁删目录**：移动失败/DB 失败分支改为只登记 `failed`，
   与正常/过期路径一起在 `try/finally` 中于锁外删除。
2. **用例依赖环境 provider**：同值保存用例固定 `VOICE_PROVIDER=unavailable`，
   不再受开发者 `.env` 里 `mock` 的影响。

## 4.5 第六轮复核修复

第六轮只读复核结论为 **0 P0 / 0 P1 / 0 P2 / 5 P3**（其中 3 条为既有缺口），均处理：

1. **失败路径清理不完整**：暂存目录在 `_PENDING.pop` 后立刻登记（`mkdir` 失败也
   不漏）；移动失败时把可能残留的半个正式目录一并登记；DB 失败只登记新目录，
   不再重复登记 `stale`。
2. **用例挡不住回退**：`test_confirm_db_failure_cleans_new_dir_and_releases_lock`
   改为包装 `_remove_dirs` 并断言调用时 `_lock` 未持有（旧实现上确实失败）。
3. **文档表述**：区分 `confirm` 的 `try/finally`（正常/过期/非取消异常）与
   `rollback` 的锁外删除；取消时 `finally` 仍会执行，但 `dest` 当时尚未登记、
   且 `finally` 内的 `await` 可能被再次取消。

## 4.6 第七轮（收尾）

第七轮只读复核结论为 **0 P0 / 0 P1 / 0 P2 / 2 P3**（均为可选优化），已处理：

1. **取消残留描述精确化**：`shutil.move` 是同步调用，取消落在其后的第一个
   `await`；残留是**完整**的 `dest`（无 DB 行）而非半个，且 `finally` 仍会执行。
   文档与 `service.py` 注释按此修正。
2. **两条失败路径补用例**：`mkdir` 失败清暂存目录、`move` 半拷贝失败清暂存与
   半个正式目录。已实测回退对应修复后两条用例都会失败。

## 5. 未完成项 / 阻塞

- **真机验收未做**：配对 → 列表在线态 → 遥控音量/动作/角度 → 急停 → 校准保存 → 断网重连；
  4D 追加：设置保存后语音下一轮生效、备份导入/导出、素材包上传/启用/回滚。
- 真实 DashScope/DeepSeek 云调用与真机音频仍未验证（沿用 Phase 3 的待办）。
- 设备端素材包下载/热替换属于 Phase 5，本块只在服务端与 App 管理。
- 已记录但无 UI 消费方：`DELETE /api/settings/{key}`（取消普通字段覆盖）本次未实现。
- `test_audio_pipeline.py::test_frames_are_dropped_during_thinking_and_speaking` 为既有失败，
  需要单独排查环境/依赖漂移，不能与 4D 混在一起改。
- `confirm` 在 `shutil.move` 成功之后、DB 提交之前被取消（`shutil.move` 同步，取消落在
  下一个 `await`；停机/断连极少见）会残留一份完整正式目录（无 DB 行，启动无孤儿目录
  清理）：正常失败路径已清理，取消路径没有 GC 兜底，触发面窄，仅记录不改。

## 6. 安全与隐私检查

- 服务端响应只返回 `configured` + `source` + 普通字段；任何 `sk-*`、Bearer Token、
  备份正文、聊天内容都不出现在响应、日志或测试断言中。
- 密钥落盘前 `encrypt_secret(..., aad=key)`；解密判定按 `SECRET_KEYS` 白名单，
  不信任行上的 `is_secret`。
- 备份预览/确认按 App Token 隔离；素材预览/确认/回滚同样按 Token 隔离，未知/过期/他人
  统一 `upload_expired` / `preview_expired`，避免探测。
- 新代码没有自定义 `X509TrustManager`/`HostnameVerifier`，没有关闭证书校验，没有 WebView。

## 7. 下一步

1. 真机联调（含 4D 的设置/备份/素材路径）。
2. 进入 Phase 5（设备端素材包下载与热替换）前，先确认真机上的占位图与帧读取耗时。
