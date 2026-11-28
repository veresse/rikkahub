#!/usr/bin/env bash
# auto-sync-upstream.yml 的共享函数。本文件被 source，不直接执行。
#
# 约定：
#   HANDOFF_BRANCH  交接分支。同步受阻时把待处理内容推到该分支并开启/刷新一个指向 PR_BASE 的 PR，
#                   作为唯一且持久的人工处理入口；恢复后自动关闭。
#   PR_BASE         PR 的目标分支（默认 main；演练时为临时分支）。
#   GITHUB_REPOSITORY、GH_TOKEN 由工作流注入。
# shellcheck shell=bash

HANDOFF_BRANCH="${HANDOFF_BRANCH:-sync/handoff}"
PR_BASE="${PR_BASE:-main}"
HANDOFF_LABEL="sync-handoff"
BOT_SHA_MARK="sync-handoff-bot-sha"
FINGERPRINT_MARK="sync-handoff"

# retry <次数> <初始间隔秒> <命令...>：网络类命令失败后按指数退避重试。
retry() {
  local attempts=$1 delay=$2
  shift 2
  local attempt=1
  while true; do
    if "$@"; then
      return 0
    fi
    if [ "$attempt" -ge "$attempts" ]; then
      echo "::error::命令重试 $attempts 次后仍失败: $*"
      return 1
    fi
    echo "::notice::第 $attempt/$attempts 次尝试失败，${delay}s 后重试: $*"
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# GitHub 不允许工作流自带的 GITHUB_TOKEN 推送改动 .github/workflows 的提交（即使只是把旧提交推到新分支）。
# 仓库 Secret SYNC_PUSH_TOKEN（可选，具备 repo + workflow 权限的 PAT）存在时，推送改用它。
push_origin() {
  if [ -n "${SYNC_PUSH_TOKEN:-}" ]; then
    git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$SYNC_PUSH_TOKEN" | base64 | tr -d '\n')" \
      push origin "$@"
  else
    git push origin "$@"
  fi
}

# push_guarded <git push 参数...>
# 返回 0 成功；2 被 GitHub 的工作流文件保护拒绝（重试没有意义）；1 其他失败（已重试 3 次）。
push_guarded() {
  local attempt=1 delay=5 out
  while true; do
    out=$(push_origin "$@" 2>&1) && { printf '%s\n' "$out"; return 0; }
    printf '%s\n' "$out"
    if printf '%s\n' "$out" | grep -q 'refusing to allow .* workflow'; then
      echo "::notice::GitHub 拒绝了推送：提交改动了 .github/workflows 下的文件，而当前令牌没有 workflow 权限。"
      return 2
    fi
    if [ "$attempt" -ge 3 ]; then
      echo "::error::命令重试 3 次后仍失败: git push $*"
      return 1
    fi
    echo "::notice::第 $attempt/3 次推送失败，${delay}s 后重试"
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# handoff_note_commit <正文文件>
# 在自定义代码基线（HANDOFF_NOTE_PARENT，默认 HEAD）上提交一份处理说明。
# 当待交接的提交因工作流文件保护无法推送时，用它作为交接分支的内容。
handoff_note_commit() {
  git checkout -q --detach "${HANDOFF_NOTE_PARENT:-HEAD}"
  mkdir -p .github
  cp "$1" .github/SYNC_HANDOFF.md
  git add .github/SYNC_HANDOFF.md
  git commit -q -m "chore(auto-sync): 上游同步受阻，处理说明见 PR 正文 [skip ci]"
  git rev-parse HEAD
}

handoff_pr_number() {
  retry 3 5 gh pr list --repo "$GITHUB_REPOSITORY" --state open --base "$PR_BASE" --head "$HANDOFF_BRANCH" \
    --json number --jq '.[0].number // empty'
}

handoff_remote_sha() {
  local out
  out=$(retry 3 5 git ls-remote origin "refs/heads/$HANDOFF_BRANCH") || return 1
  printf '%s' "${out%%[[:space:]]*}"
}

# _handoff_push <sha> <远程现有提交> <PR 中记录的机器人提交>：把交接分支更新到 <sha>，不覆盖人工提交。
# 返回值同 push_guarded（2 = 工作流文件保护拒绝，1 = 其他失败）；分支含人工提交时不推送，返回 3。
_handoff_push() {
  local sha=$1 remote=$2 recorded=$3
  if [ -z "$remote" ]; then
    push_guarded "${sha}:refs/heads/$HANDOFF_BRANCH"
  elif [ -z "$recorded" ] || [ "$remote" = "$recorded" ]; then
    push_guarded "--force-with-lease=refs/heads/$HANDOFF_BRANCH:$remote" "${sha}:refs/heads/$HANDOFF_BRANCH"
  elif git merge-base --is-ancestor "$remote" "$sha"; then
    push_guarded "${sha}:refs/heads/$HANDOFF_BRANCH"
  else
    echo "::notice::交接分支 $HANDOFF_BRANCH 上有人工提交，保留分支不动，仅刷新 PR。"
    return 3
  fi
}

# handoff_publish <标题> <正文文件> <要置于交接分支的提交>
# 第 3 个参数为空表示不推送合并/上游提交，交接分支只放处理说明。
#
# 交接分支只允许机器人改写自己上次推送的提交（记录在 PR 正文的隐藏标记里）；
# 如果分支上出现了人工提交（例如在网页上解决过冲突），保留分支不动，只刷新 PR。
handoff_publish() {
  local title=$1 body_file=$2 sha=$3
  local number recorded="" remote body note_parent="${HANDOFF_NOTE_PARENT:-HEAD}"
  number=$(handoff_pr_number) || return 1
  remote=$(handoff_remote_sha) || return 1
  if [ -n "$number" ]; then
    body=$(gh pr view "$number" --repo "$GITHUB_REPOSITORY" --json body --jq .body) || return 1
    recorded=$(printf '%s' "$body" | sed -n "s/.*<!-- ${BOT_SHA_MARK}: \([0-9a-f]*\) *-->.*/\1/p")
  fi

  local status=0 note=0 marker_sha=""
  if [ -n "$sha" ]; then
    sha=$(git rev-parse --verify "${sha}^{commit}") || return 1
    _handoff_push "$sha" "$remote" "$recorded" || status=$?
  else
    status=2
  fi
  if [ "$status" -eq 2 ]; then
    # 自动令牌推不动含工作流文件改动的提交：降级为只含处理说明的分支
    HANDOFF_NOTE_PARENT="$note_parent" sha=$(handoff_note_commit "$body_file") || return 1
    note=1
    remote=$(handoff_remote_sha) || return 1
    status=0
    _handoff_push "$sha" "$remote" "$recorded" || status=$?
  fi
  case "$status" in
    0) marker_sha=$sha ;;
    3) marker_sha=$recorded ;;
    *) echo "::error::交接分支 $HANDOFF_BRANCH 推送失败。"; return 1 ;;
  esac

  # 正文（参与"原因是否变化"的判断）+ 页脚（最近一次检查的运行链接，不参与判断）
  local final="${RUNNER_TEMP:-/tmp}/handoff-body-${GITHUB_RUN_ID:-0}.md"
  {
    if [ "$note" -eq 1 ]; then
      echo '> **注意**：GitHub 不允许自动同步使用的令牌推送改动工作流文件的提交，本 PR 的分支只包含这份处理说明，网页上的 “Resolve conflicts” 不可用，请按 **方式 B（本地）** 处理。'
      echo
    fi
    cat "$body_file"
    printf '\n---\n最近一次检查：[运行 #%s](%s)\n\n' "${GITHUB_RUN_NUMBER:-0}" \
      "${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID:-0}"
    printf '<!-- %s: %s -->\n' "$BOT_SHA_MARK" "$marker_sha"
  } > "$final"

  gh label create "$HANDOFF_LABEL" --repo "$GITHUB_REPOSITORY" --color D93F0B \
    --description "上游自动同步受阻，需要人工处理" >/dev/null 2>&1 || true

  if [ -z "$number" ]; then
    retry 3 10 gh pr create --repo "$GITHUB_REPOSITORY" --base "$PR_BASE" --head "$HANDOFF_BRANCH" \
      --title "$title" --body-file "$final" --label "$HANDOFF_LABEL" || return 1
    number=$(handoff_pr_number) || return 1
    echo "已创建交接 PR #$number"
  else
    retry 3 10 gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title "$title" --body-file "$final" || return 1
    echo "已刷新交接 PR #$number"
  fi
  printf 'handoff_pr=%s\n' "$number" >> "${GITHUB_OUTPUT:-/dev/null}"

  # 只有受阻原因发生变化时才追加评论并触发通知，避免每次定时运行都打扰。
  local fingerprint comments
  fingerprint=$(printf '%s\n%s' "$title" "$(cat "$body_file")" | sha256sum | cut -c1-12)
  comments=$(gh pr view "$number" --repo "$GITHUB_REPOSITORY" --json comments --jq '.[].body' 2>/dev/null || true)
  if ! printf '%s' "$comments" | grep -q "<!-- ${FINGERPRINT_MARK}: ${fingerprint} -->"; then
    retry 3 10 gh pr comment "$number" --repo "$GITHUB_REPOSITORY" \
      --body "<!-- ${FINGERPRINT_MARK}: ${fingerprint} -->
$(cat "$body_file")" >/dev/null && echo "已追加评论（原因指纹 ${fingerprint}）"
  fi
}

# handoff_resolve <说明>：恢复后关闭交接 PR，并清理没有人工提交的交接分支。
handoff_resolve() {
  local note=$1 number remote="" recorded="" body
  number=$(handoff_pr_number) || return 0
  remote=$(handoff_remote_sha) || true
  if [ -z "$number" ] && [ -z "$remote" ]; then
    return 0
  fi
  if [ -n "$number" ]; then
    body=$(gh pr view "$number" --repo "$GITHUB_REPOSITORY" --json body --jq .body 2>/dev/null || true)
    recorded=$(printf '%s' "$body" | sed -n "s/.*<!-- ${BOT_SHA_MARK}: \([0-9a-f]*\) *-->.*/\1/p")
    gh pr comment "$number" --repo "$GITHUB_REPOSITORY" --body "$note" >/dev/null || true
    gh pr close "$number" --repo "$GITHUB_REPOSITORY" >/dev/null || true
    printf 'handoff_pr=%s\n' "$number" >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "已关闭交接 PR #$number"
  else
    recorded=$remote
  fi
  if [ -z "$remote" ]; then
    return 0
  fi
  if [ "$remote" = "$recorded" ]; then
    retry 3 5 push_origin --delete "$HANDOFF_BRANCH" || true
  else
    echo "::notice::交接分支 $HANDOFF_BRANCH 含有人工提交，未删除。"
  fi
}

# drill_cleanup：演练结束后关闭交接 PR 并删除临时分支，不留任何痕迹。
drill_cleanup() {
  local number
  number=$(handoff_pr_number) || number=""
  if [ -n "$number" ]; then
    gh pr comment "$number" --repo "$GITHUB_REPOSITORY" --body "演练结束，自动关闭。" >/dev/null || true
    gh pr close "$number" --repo "$GITHUB_REPOSITORY" >/dev/null || true
  fi
  retry 3 5 push_origin --delete "$HANDOFF_BRANCH" "${DRILL_BASE_BRANCH:-sync/drill-base}" >/dev/null 2>&1 || true
}
