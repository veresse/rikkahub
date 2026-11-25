# 02 · Block A（Phase 1）：动作库与 companion.v2 协议定稿

- 日期：2026-11-25
- 状态：✅ 完成
- 分支：`feature/companion-robot-v2`
- 对照计划：`v1_v2.md` Phase 1

## 1. 目标与范围

建立 Server / Android App / ESP32 固件三端共享的协议契约与动作目录，先定规范再写实现：

1. `companion/shared/actions.json`：18 个标准动作、参数档位、安全限位、关键帧、联动表情、时长上限。
2. `companion/shared/actions.schema.json`：JSON Schema。
3. `companion/shared/catalog.py`：加载、结构性校验、参数解析、LLM 工具菜单生成。
4. `companion/shared/validate_actions.py`：CLI 校验入口。
5. `companion/docs/protocol_v2.md`：`companion.v2` 设备/App 协议规范。
6. `companion/tests/test_actions_catalog.py` + `test_protocol_v2_doc.py`：契约测试。

**本阶段未修改任何现有业务代码/数据库/API。**

## 2. 改动文件清单

| 类型 | 路径 |
| :--- | :--- |
| 新增 | `companion/shared/actions.json` |
| 新增 | `companion/shared/actions.schema.json` |
| 新增 | `companion/shared/catalog.py` |
| 新增 | `companion/shared/validate_actions.py` |
| 新增 | `companion/shared/__init__.py` |
| 新增 | `companion/docs/protocol_v2.md` |
| 新增 | `companion/tests/test_actions_catalog.py` |
| 新增 | `companion/tests/test_protocol_v2_doc.py` |
| 新增 | `conftest.py`（测试数据库隔离引导） |
| 修改 | `companion/server/requirements.txt`（新增测试依赖 `jsonschema>=4.23.0`） |

## 3. 动作库设计要点

- **18 个动作**：`home / nod / shake / look_left / look_right / look_up / look_down / look_around / bow / peek / surprised / sleep / wake / panic / swing / tilt_left / tilt_right / dance`。
- **统一角度口径**：`pan ∈ [-90, 90]`、`tilt ∈ [-20, 45]`、`volume ∈ [0, 100]`。
- **档位**：`intensity 1/2/3`、`speed 1/2/3`（单程 900/500/300ms）、`repeat 1..5`。
- **关键帧**：以 `intensity=3`（满行程）编写，动作自带 `intensity_scale`（长度 3、非递减、第三档必须 1.0），固件按档位缩放。
- **时长**：普通动作 `max_duration_ms = 3000`；只有 `dance` 放宽到 `5000`；校验器会拒绝其他动作超过 3000。
- **联动表情**：`surprised → surprised`、`sleep → sleep`、`wake → idle`。
- **模型语义**：每个动作带 `llm_description_zh`，供 Phase 2 动态生成 DeepSeek 工具 description。

示例：`look_left` 满行程 `-75°`，`intensity_scale = [0.33, 0.6, 1.0]` → 轻/中/用力 = `-25 / -45 / -75`，与第二版方案一致。

## 4. 协议要点

`companion/docs/protocol_v2.md` 定稿了：

- 设备 `/device/ws` 与 App `/app/ws` 的 Bearer Header 鉴权、关闭码、心跳。
- 配对流程（一次性 6 位码、限时限次限速、仅一次返回明文设备 Token）。
- 消息信封 `v/type/session_id/turn_id/ts`。
- 下行：`action.play`、`servo.move`、`volume.set`、`expression.set`、`idle.set`、`action.stop`、校准消息。
- 上行：`action.result`、`device.report`、`voice.event`。
- 12 字节音频帧头（magic `0xA1`、kind、flags、seq、turn_seq、Opus payload）。
- 双源仲裁：`action.stop` 最高优先级、音量/位置 Last-Write-Wins、`action.play` FIFO、离线直接失败不补发。
- App 遥控消息与事件广播。
- 错误码与安全红线（Token 哈希、TLS、不落盘音频）。

## 5. 实际执行的命令与真实输出

### 5.1 动作库 / 协议测试

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
......................................                                   [100%]
38 passed in 3.70s
```

再次运行（验证测试库隔离、无脏数据）：

```text
$ & "...\companion-venv\Scripts\python.exe" -m pytest -q
......................................                                   [100%]
38 passed in 2.61s
```

### 5.2 动作库 CLI 校验

```text
$ & "...\companion-venv\Scripts\python.exe" -m companion.shared.validate_actions
OK: actions=18 version=2.0.0 default_max=3000ms hard_max=5000ms
```

## 6. 与计划的偏差 / Ruling

| Ruling | 原因 | 代价（如果判断错） |
| :--- | :--- | :--- |
| `actions.json` 的关键帧按 `intensity=3` 编写，并用每个动作自己的 `intensity_scale` 缩放，而不是统一 0.33/0.67/1.0 | 第二版方案对 `look_left/right`（-25/-45/-75）与 `look_up`（-8/-14/-20）给出了非线性的具体值；统一比例无法同时满足 | 固件与 App 多一个字段解析；若后续想改回统一比例，只需改 JSON |
| 新增根 `conftest.py` 强制隔离测试 `DATA_DIR` | 发现 v1 测试存在隐藏的脏数据 bug：`Settings` 模块级单例在测试模块导入后才设置 `DATA_DIR`，第二次运行会复用 `companion/server/data/companion.db`，导致 `test_full_system` 失败。这是 v1 既有问题，不是本次功能引入 | 若项目本来依赖固定测试库路径，需要调整；当前所有测试均通过 |
| 新增 `jsonschema` 测试依赖 | Phase 1 验收要求 JSON Schema 校验 | 增加一个纯 Python 依赖，无运行时影响 |
| 协议文档同时定稿了音频帧头与状态机 | 仓库 v1 实际没有设备 WS/音频实现，若不现在定稿，Phase 2/3 的网关与模拟器无契约可依 | 若后续决定不做音频链路，该章节作为未来扩展保留，不影响控制面实现 |
| 未删除 `companion/web`、未改任何现有 API | 避免在 App 未交付前出现管理入口空窗；范围待负责人确认 | 若负责人要求立即删除，可在 Block B 一并处理 |

## 7. 未完成项 / 阻塞点

- 仍未实现任何服务端运行时代码（鉴权、设备网关、动作编排、App 网关）——属于 Block B–D。
- `companion/shared/actions.json` 尚未被 Server 工具 Schema、App、固件实际消费；消费方在后续 Block 接入。
- 动作库的实机角度/时长仍需真机校准后回填（`intensity_scale` 与 `max_duration_ms` 可能微调）。

## 8. 下一步

进入 **Block B（Phase 2 基础）：Alembic 可逆迁移 + App/设备鉴权 + Token 哈希存储 + 配置与错误码**。该 Block 开始会改动现有数据库与 API，需负责人确认范围后再执行。
