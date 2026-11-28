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

handoff_pr_number() {
  retry 3 5 gh pr list --repo "$GITHUB_REPOSITORY" --state open --base "$PR_BASE" --head "$HANDOFF_BRANCH" \
    --json number --jq '.[0].number // empty'
}

handoff_remote_sha() {
  local out
  out=$(retry 3 5 git ls-remote origin "refs/heads/$HANDOFF_BRANCH") || return 1
  printf '%s' "${out%%[[:space:]]*}"
}

# handoff_publish <标题> <正文文件> <要置于交接分支的提交>
#
# 交接分支只允许机器人改写自己上次推送的提交（记录在 PR 正文的隐藏标记里）；
# 如果分支上出现了人工提交（例如在网页上解决过冲突），保留分支不动，只刷新 PR。
handoff_publish() {
  local title=$1 body_file=$2 sha=$3
  sha=$(git rev-parse --verify "${sha}^{commit}") || return 1
  local number recorded="" remote body
  number=$(handoff_pr_number) || return 1
  remote=$(handoff_remote_sha) || return 1
  if [ -n "$number" ]; then
    body=$(gh pr view "$number" --repo "$GITHUB_REPOSITORY" --json body --jq .body) || return 1
    recorded=$(printf '%s' "$body" | sed -n "s/.*<!-- ${BOT_SHA_MARK}: \([0-9a-f]*\) *-->.*/\1/p")
  fi

  local marker_sha=$sha
  if [ -z "$remote" ]; then
    retry 3 10 git push origin "${sha}:refs/heads/$HANDOFF_BRANCH" || return 1
  elif [ -z "$recorded" ] || [ "$remote" = "$recorded" ]; then
    retry 3 10 git push "--force-with-lease=refs/heads/$HANDOFF_BRANCH:$remote" origin \
      "${sha}:refs/heads/$HANDOFF_BRANCH" || return 1
  elif git merge-base --is-ancestor "$remote" "$sha"; then
    retry 3 10 git push origin "${sha}:refs/heads/$HANDOFF_BRANCH" || return 1
  else
    echo "::notice::交接分支 $HANDOFF_BRANCH 上有人工提交，保留分支不动，仅刷新 PR。"
    marker_sha=$recorded
  fi

  # 正文（参与"原因是否变化"的判断）+ 页脚（最近一次检查的运行链接，不参与判断）
  local final="${RUNNER_TEMP:-/tmp}/handoff-body-${GITHUB_RUN_ID:-0}.md"
  {
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
    retry 3 5 git push origin --delete "$HANDOFF_BRANCH" || true
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
  retry 3 5 git push origin --delete "$HANDOFF_BRANCH" "${DRILL_BASE_BRANCH:-sync/drill-base}" >/dev/null 2>&1 || true
}
