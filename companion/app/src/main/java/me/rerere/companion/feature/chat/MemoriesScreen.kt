package me.rerere.companion.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import me.rerere.companion.data.api.dto.MemoryDto

/**
 * 角色记忆列表（[assistantId] 为 null 时是全局记忆）。
 *
 * 记忆正文只在本机内存/服务端，不打印、不写日志。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemoriesScreen(
    assistantName: String,
    state: MemoriesUiState,
    online: Boolean,
    onRefresh: () -> Unit,
    onCreate: () -> Unit,
    onEdit: (MemoryDto) -> Unit,
    onDelete: (String) -> Unit,
    onEditorChange: (String) -> Unit,
    onSave: () -> Unit,
    onCloseEditor: () -> Unit,
    onDismissSnackbar: () -> Unit,
    onBack: () -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    val busy = state.saving || state.deletingId != null
    LaunchedEffect(state.snackbar) {
        val message = state.snackbar ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(message)
        onDismissSnackbar()
    }

    var deleteTarget by remember { mutableStateOf<MemoryDto?>(null) }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
                            Text("记忆")
                            Text(
                                text = assistantName.ifBlank { "全局" },
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    },
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !busy) { Text("返回") }
                    },
                    actions = {
                        TextButton(onClick = onRefresh, enabled = online && !state.loading) {
                            Text("刷新")
                        }
                    },
                )
                if (!online) {
                    OfflineBanner("当前离线，新增 / 编辑 / 删除已禁用")
                }
            }
        },
        floatingActionButton = {
            if (online) {
                ExtendedFloatingActionButton(
                    onClick = onCreate,
                    content = { Text("新增记忆") },
                )
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when {
                state.loading && state.items.isEmpty() -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }

                state.error != null && state.items.isEmpty() -> {
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
                        Button(onClick = onRefresh, enabled = online) { Text("重试") }
                    }
                }

                state.items.isEmpty() -> {
                    Column(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("还没有记忆", style = MaterialTheme.typography.titleMedium)
                        Button(onClick = onCreate, enabled = online) { Text("新增记忆") }
                    }
                }

                else -> {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(
                            start = 16.dp,
                            end = 16.dp,
                            top = 8.dp,
                            bottom = 96.dp,
                        ),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        items(state.items, key = { it.id }) { memory ->
                            MemoryCard(
                                memory = memory,
                                deleting = state.deletingId == memory.id,
                                online = online,
                                onEdit = { onEdit(memory) },
                                onDelete = { deleteTarget = memory },
                            )
                        }
                    }
                }
            }
        }
    }

    state.editor?.let { editor ->
        MemoryEditorDialog(
            content = editor.content,
            saving = state.saving,
            online = online,
            onContentChange = onEditorChange,
            onSave = onSave,
            onDismiss = onCloseEditor,
        )
    }

    deleteTarget?.let { target ->
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("删除记忆") },
            text = { Text("确定删除这条记忆吗？") },
            confirmButton = {
                TextButton(
                    onClick = {
                        deleteTarget = null
                        onDelete(target.id)
                    },
                    enabled = online,
                ) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun MemoryCard(
    memory: MemoryDto,
    deleting: Boolean,
    online: Boolean,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Text(
                text = "${if (memory.assistantId == null) "全局" else "角色"} · ${memory.path}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = memory.content,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(vertical = 6.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onEdit, enabled = online) { Text("编辑") }
                TextButton(onClick = onDelete, enabled = online && !deleting) {
                    Text(if (deleting) "删除中…" else "删除")
                }
            }
        }
    }
}

@Composable
private fun MemoryEditorDialog(
    content: String,
    saving: Boolean,
    online: Boolean,
    onContentChange: (String) -> Unit,
    onSave: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = { if (!saving) onDismiss() },
        title = { Text("记忆内容") },
        text = {
            OutlinedTextField(
                value = content,
                onValueChange = onContentChange,
                minLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            TextButton(
                onClick = onSave,
                enabled = online && !saving && content.isNotBlank(),
            ) {
                Text(if (saving) "保存中…" else "保存")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !saving) { Text("取消") }
        },
    )
}
