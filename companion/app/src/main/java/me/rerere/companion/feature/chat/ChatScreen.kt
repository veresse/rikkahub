package me.rerere.companion.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 流式聊天页。
 *
 * 页面只做渲染与事件转发；生成状态、幂等重试、转写纠正全部在
 * [ChatViewModel] 里，方便 JVM 单测。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    state: ChatUiState,
    online: Boolean,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onRetry: (String) -> Unit,
    onReload: () -> Unit,
    onStartCorrection: (String) -> Unit,
    onCancelCorrection: () -> Unit,
    onCorrect: (String) -> Unit,
    onUseVoiceDraft: () -> Unit,
    onDismissVoiceDraft: () -> Unit,
    onDismissSnackbar: () -> Unit,
    onMemories: () -> Unit,
    onBack: () -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    val listState = rememberLazyListState()

    LaunchedEffect(state.snackbar) {
        val message = state.snackbar ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(message)
        onDismissSnackbar()
    }

    val lastTextLength = state.messages.lastOrNull()?.text?.length ?: 0
    // 是否跟随最新消息：初始为 true（打开长会话要落在底部），只有用户自己
    // 向上拖动时才关闭。不能在 LaunchedEffect 里现场读 `canScrollForward`：
    // 首次组合时 list 尚未布局，会读到“下方没有内容”，长历史就不会滚到底。
    var followTail by remember { mutableStateOf(true) }
    LaunchedEffect(listState) {
        snapshotFlow { listState.isScrollInProgress to !listState.canScrollForward }
            .collect { (scrolling, atBottom) ->
                // 只认用户手势期间的位置；内容增长导致的 canScrollForward 变化不算。
                if (scrolling) followTail = atBottom
            }
    }
    LaunchedEffect(state.messages.size, lastTextLength) {
        if (state.messages.isEmpty()) return@LaunchedEffect
        if (followTail) {
            listState.scrollToItem(state.messages.lastIndex)
        }
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
                            Text(state.title.ifBlank { "聊天" })
                            if (state.assistantName.isNotBlank() && state.title != state.assistantName) {
                                Text(
                                    text = state.assistantName,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    },
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !state.correcting) { Text("返回") }
                    },
                    actions = {
                        TextButton(onClick = onMemories) { Text("记忆") }
                        if (state.generating) {
                            Text(
                                text = "生成中…",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.padding(end = 8.dp),
                            )
                        }
                        TextButton(
                            onClick = onReload,
                            enabled = online && !state.loading && !state.sending && !state.generating,
                        ) {
                            Text("刷新")
                        }
                    },
                )
                if (state.generating) {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
                if (!online) {
                    OfflineBanner("当前离线，发送已禁用")
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            Box(modifier = Modifier.weight(1f)) {
                when {
                    state.loading && state.messages.isEmpty() -> {
                        CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                    }

                    state.error != null && state.messages.isEmpty() -> {
                        Column(
                            modifier = Modifier
                                .align(Alignment.Center)
                                .padding(24.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                text = state.error,
                                color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodyLarge,
                            )
                            Button(onClick = onReload, enabled = online && !state.generating) { Text("重试") }
                        }
                    }

                    state.messages.isEmpty() -> {
                        Text(
                            text = "发条消息开始聊天",
                            modifier = Modifier.align(Alignment.Center),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    else -> {
                        LazyColumn(
                            state = listState,
                            modifier = Modifier.fillMaxSize(),
                            contentPadding = PaddingValues(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            items(state.messages, key = { it.key }) { message ->
                                MessageBubble(
                                    message = message,
                                    canRetry = online && !state.sending && !state.generating,
                                    onRetry = { onRetry(message.key) },
                                    onStartCorrection = { onStartCorrection(message.key) },
                                )
                            }
                        }
                    }
                }
            }

            state.voiceDraft?.let { draft ->
                VoiceDraftCard(
                    draft = draft,
                    onUse = onUseVoiceDraft,
                    onDismiss = onDismissVoiceDraft,
                )
            }

            InputBar(
                state = state,
                online = online,
                onInputChange = onInputChange,
                onSend = onSend,
                onStop = onStop,
            )
        }
    }

    state.correctionTarget?.let { target ->
        CorrectionDialog(
            target = target,
            online = online,
            saving = state.correcting,
            onConfirm = onCorrect,
            onDismiss = onCancelCorrection,
        )
    }
}

@Composable
private fun MessageBubble(
    message: ChatMessageUi,
    canRetry: Boolean,
    onRetry: () -> Unit,
    onStartCorrection: () -> Unit,
) {
    val isUser = message.role == "USER"
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Column(
            modifier = Modifier.widthIn(max = 320.dp),
            horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
        ) {
            Surface(
                shape = RoundedCornerShape(
                    topStart = 18.dp,
                    topEnd = 18.dp,
                    bottomStart = if (isUser) 18.dp else 4.dp,
                    bottomEnd = if (isUser) 4.dp else 18.dp,
                ),
                color = if (isUser) {
                    MaterialTheme.colorScheme.primaryContainer
                } else {
                    MaterialTheme.colorScheme.surfaceContainerHigh
                },
            ) {
                Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                    if (!isUser && message.reasoning.isNotBlank()) {
                        Text(
                            text = message.reasoning,
                            style = MaterialTheme.typography.bodySmall,
                            fontStyle = FontStyle.Italic,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    when {
                        message.text.isNotBlank() -> Text(
                            text = message.text,
                            style = MaterialTheme.typography.bodyLarge,
                        )

                        message.streaming -> Text(
                            text = "…",
                            style = MaterialTheme.typography.bodyLarge,
                        )
                    }
                }
            }

            when {
                message.streaming -> Text(
                    text = "生成中…",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                message.interrupted -> Text(
                    text = "已中断（回答可能不完整）",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                message.failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "生成失败",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    TextButton(onClick = onRetry, enabled = canRetry) { Text("重试") }
                }
            }

            if (isUser && message.userMessageId != null && !message.streaming) {
                TextButton(onClick = onStartCorrection) {
                    Text("纠正转写", fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun VoiceDraftCard(
    draft: String,
    onUse: () -> Unit,
    onDismiss: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.secondaryContainer,
    ) {
        Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Text(
                text = "语音转写草稿（尚未发送）",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
            Text(
                text = draft,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(vertical = 4.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onUse) { Text("填入输入框") }
                TextButton(onClick = onDismiss) { Text("忽略") }
            }
        }
    }
}

@Composable
private fun InputBar(
    state: ChatUiState,
    online: Boolean,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
) {
    Surface(tonalElevation = 3.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = state.input,
                onValueChange = onInputChange,
                modifier = Modifier.weight(1f),
                maxLines = 4,
                placeholder = {
                    Text(if (online) "输入消息…" else "当前离线")
                },
            )
            if (state.sending) {
                TextButton(onClick = onStop) { Text("停止") }
            } else {
                TextButton(
                    onClick = onSend,
                    enabled = online && !state.generating && !state.loading && state.input.isNotBlank(),
                ) {
                    Text(if (state.generating) "生成中…" else "发送")
                }
            }
        }
    }
}

@Composable
private fun CorrectionDialog(
    target: ChatMessageUi,
    online: Boolean,
    saving: Boolean,
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var text by remember(target.key) { mutableStateOf(target.text) }

    AlertDialog(
        onDismissRequest = { if (!saving) onDismiss() },
        title = { Text("纠正用户消息") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    text = "只修改已保存的用户文本，不会自动重新生成回答。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(text) },
                enabled = online && !saving && text.isNotBlank(),
            ) {
                Text(if (saving) "保存中…" else "保存")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !saving) { Text("取消") }
        },
    )
}
