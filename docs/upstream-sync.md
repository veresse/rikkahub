# 上游同步流程

`Auto Sync Upstream` 工作流（`.github/workflows/auto-sync-upstream.yml`）每天 4 次（北京时间 02:00、08:00、14:00、20:00）检查原作者仓库 `rikkahub/rikkahub` 的 `master`，也可以在 Actions 页面手动运行。

## 原则

**main 只包含已经构建通过、单元测试通过并已发布的代码。** 任何一步受阻都不改动 main、不发布版本，因此手机端应用永远不会通过应用内更新收到一个无法构建的版本。

## 流程

1. **拉取上游**：带重试（3 次，指数退避），应对网络抖动。若已追平上游则结束，并关闭遗留的交接 PR。
2. **合并上游**：`git merge`（diff3 冲突格式）。双方都只是在同一位置新增内容的 XML 冲突（清单、`res/values*` 资源）由 `.github/scripts/sync/auto_resolve.py` 自动保留双方内容，其余冲突交给人工。
3. **自定义修改保全校验**：`.github/scripts/sync/verify_fork.py`
   - 检查 `fork-markers.tsv` 中列出的挂载点仍然存在（权限、组件、依赖注入、路由、设置入口、更新地址、数据库版本等）；
   - 逐行确认合并前本仓库相对共同祖先新增的每一行在合并结果中仍然存在。
4. **推送到 `sync/staging` 分支**，由 `Build Custom Release APK` 工作流编译 Release 包、运行单元测试（`:app:testGithubDebugUnitTest`，包含自定义功能的回归测试），通过后以草稿上传全部 APK 再公开 Release。
5. **快进 main** 到已发布的合并提交，关闭交接 PR，清理 staging 分支，并确认 `releases/latest` 已返回新版本（应用内更新读取这个地址）。

合并提交带 `[skip ci]`，不会重复触发手动推送用的构建；版本号、签名沿用既有配置，手机端可直接覆盖安装。

## 受阻时的处理

受阻时工作流会创建或刷新一个标题以 “⚠️ 上游同步” 开头、带 `sync-handoff` 标签的 PR（分支 `sync/handoff`），正文写明原因、涉及的文件和上游提交列表。这是唯一的人工处理入口；原因没有变化时只刷新正文，不重复评论。

| 情况 | 自动行为 | 人工处理 |
| --- | --- | --- |
| 合并冲突（无法自动解决） | 交接 PR 显示冲突 | 在 PR 中点 “Resolve conflicts”，或本地 `git fetch upstream && git merge upstream/master` 后推送 main |
| 保全校验未通过 | 交接 PR 指向合并结果 | 把自定义功能重新接入被上游重构的位置，必要时更新 `fork-markers.tsv` |
| 编译或单元测试失败 | 交接 PR 指向 `sync/staging` 的合并结果 | 在该运行的 Artifacts 中查看单元测试报告，修复后合并或推送 main |
| 构建期间 main 有新提交 | 放弃本次快进并报错 | 无需处理，下一次定时同步会基于新的 main 重新合并 |
| 拉取或推送失败 | 自动重试 3 次 | 仍失败时查看日志；下一次定时运行会再次尝试 |

处理完成后用 **Create a merge commit** 合并 PR（不要 Squash，否则上游提交不会成为 main 的祖先，下次同步会重复处理）。推送 main 会自动构建发布，下一次定时同步确认已追平上游后会自动关闭交接 PR。在 `sync/handoff` 分支上留下的人工提交不会被机器人覆盖或删除。

## 维护

- 新增自定义功能时，把它的挂载点加进 `.github/scripts/sync/fork-markers.tsv`（`exists` 或 `contains`）。
- 上游调整数据库版本或重构挂载点时，保全校验会阻止自动合并，需要人工评估迁移路径并更新标记表。
- 演练告警链路：手动运行工作流并填写 `drill_base_ref`（一个尚未合入某些上游提交的旧提交）和可选的 `drill_upstream_ref`。演练会重放一次冲突处理，创建带 “[演练]” 前缀的交接 PR 后自动关闭并清理临时分支，不改动 main、不发布。
