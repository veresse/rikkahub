## 上游同步受阻

- 上游新增提交：12 个（上游提交 `c08d7200`）
- 本仓库自定义提交：22 个（相对上游）

上游新增的 12 个提交与自定义代码在以下文件产生冲突，无法安全自动合并：

- `app/src/github/AndroidManifest.xml`

自动解决只处理"双方都只是新增内容"的 XML 冲突；其余情况需要人工判断。

### 处理方式
**方式 A（网页）**：点击本 PR 中的 “Resolve conflicts”，逐个文件解决后选择 “Commit merge”，再用 **Create a merge commit** 合并本 PR。不要使用 Squash merge，否则上游提交不会成为 main 的祖先，下次同步会重复处理。

**方式 B（本地）**
```bash
git fetch upstream
git merge c08d720036857445038249220a9168009e3160a0
# 逐个解决冲突后：git add <文件>
./gradlew :app:testGithubDebugUnitTest
git commit && git push origin main
```

两种方式推送到 main 后都会自动构建并发布，手机端应用可通过应用内更新收到。下一次定时同步确认已追平上游后会自动关闭本 PR。

<details><summary>上游提交列表</summary>

- c08d7200 refactor(chat): 移除聊天页未配置模型时的快速入门卡片
- 55a0631f feat(workspace): Shell 工具新增可选 description 参数并作为步骤标题显示
- 2afc907d feat(chat): 发送消息后用户消息滚到顶部，回复在下方展开不跟随滚动
- 3aade2e5 fix(chat): 长时间工具调用中途定期落库，避免崩溃后整轮记录丢失
- b2b4eca6 fix(floating): 修复悬浮球变形阴影缓存无限增长导致的 OOM
- 1fb09152 fix(provider): 连接测试使用模型的提供商重写配置
- e258d9de perf(mediagen): generate / watch 默认轮询间隔由 15 秒改为 5 秒
- 22b3473d feat(mediagen): 新增 APIMart 提供商，支持 GPT-Image 图像与 MiniMax H3 视频
- eb417de2 feat(notification): 通知设置新增忽略电池优化入口
- 7f11a5f1 feat(chat): 手动压缩上下文改为在对话中后台进行
- cc873543 feat(asr): 语音输入录音时保持屏幕常亮
- f37f66b3 fix(chat): 降低发送图片的像素上限并在 OOM 时提示错误

</details>
