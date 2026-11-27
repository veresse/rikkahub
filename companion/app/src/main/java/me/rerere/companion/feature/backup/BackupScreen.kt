package me.rerere.companion.feature.backup

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import me.rerere.companion.data.api.dto.BackupPreviewDto

/**
 * 备份页写操作门控：手机有网且没有在途的预览/导入/导出。
 *
 * 与设置页、遥控页保持一致：离线禁用只是第一道，ViewModel 里还会再挡一次。
 */
fun backupActionsEnabled(online: Boolean, busy: Boolean): Boolean = online && !busy

/**
 * 备份与恢复：导出服务器数据，或先预览再导入备份包。
 *
 * 文件选择/创建都用系统文件选择器，页面只把“打开流”的 lambda 交给
 * ViewModel，不把整包内容读进内存。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BackupScreen(
    state: BackupUiState,
    online: Boolean,
    onPreview: (() -> java.io.InputStream?) -> Unit,
    onExport: (() -> java.io.OutputStream?) -> Unit,
    onConfirm: () -> Unit,
    onDismissError: () -> Unit,
    onBack: () -> Unit,
) {
    val busy = state.previewing || state.importing || state.exporting
    val actionsEnabled = backupActionsEnabled(online, busy)
    var confirmImport by remember { mutableStateOf(false) }

    // 文件选择/创建都走系统选择器；页面只交出“打开流”的 lambda，
    // ViewModel 决定是否读取并负责关闭，离线/在途时不会泄漏流。
    val context = LocalContext.current
    val openDocument = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        onPreview(
            if (uri == null) {
                { null }
            } else {
                { runCatching { context.contentResolver.openInputStream(uri) }.getOrNull() }
            },
        )
    }
    val createDocument = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/zip"),
    ) { uri ->
        onExport(
            if (uri == null) {
                { null }
            } else {
                { runCatching { context.contentResolver.openOutputStream(uri) }.getOrNull() }
            },
        )
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("备份与恢复") },
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !busy) { Text("返回") }
                    },
                )
                if (!online) {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        color = MaterialTheme.colorScheme.errorContainer,
                    ) {
                        Text(
                            text = "当前离线，导入 / 导出已禁用",
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                        )
                    }
                }
                if (busy) {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                SectionCard(title = "导出备份") {
                    Text(
                        text = "把当前服务器的角色、会话、消息分支与记忆打包成 RikkaHub 兼容 ZIP。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Button(onClick = { createDocument.launch("rikkahub_backup.zip") }, enabled = actionsEnabled) {
                        Text(if (state.exporting) "导出中…" else "导出备份")
                    }
                    state.exportedBytes?.let { bytes ->
                        Text(
                            text = "最近一次导出：${formatMb(bytes)}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            item {
                SectionCard(title = "导入备份") {
                    Text(
                        text = "先选择 .zip 备份文件预览统计，确认后再写入数据库。预览 10 分钟内有效，且只属于当前登录。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(
                        onClick = {
                            openDocument.launch(
                                arrayOf("application/zip", "application/octet-stream"),
                            )
                        },
                        enabled = actionsEnabled,
                    ) {
                        Text(if (state.previewing) "解析中…" else "选择备份文件")
                    }
                }
            }

            state.preview?.let { preview ->
                item { PreviewCard(preview = preview) }
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(
                            onClick = { confirmImport = true },
                            enabled = actionsEnabled,
                        ) { Text(if (state.importing) "导入中…" else "确认导入") }
                    }
                }
            }

            state.result?.let { result ->
                item {
                    SectionCard(title = "导入结果") {
                        Text(
                            text = "角色 ${result.importedAssistants} · 会话 " +
                                "${result.importedConversations} · 分支节点 " +
                                "${result.importedNodes} · 记忆 ${result.importedMemories}",
                            style = MaterialTheme.typography.bodyLarge,
                        )
                        Text(
                            text = "返回角色列表后会重新拉取数据。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            state.error?.let { message ->
                item {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape = MaterialTheme.shapes.large,
                        color = MaterialTheme.colorScheme.errorContainer,
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Text(
                                text = message,
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                            FilledTonalButton(onClick = onDismissError) { Text("知道了") }
                        }
                    }
                }
            }
        }
    }

    if (confirmImport) {
        AlertDialog(
            onDismissRequest = { confirmImport = false },
            title = { Text("确认导入") },
            text = {
                Text("导入会覆盖同 id 的角色/会话并追加记忆，确定继续吗？")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmImport = false
                        onConfirm()
                    },
                ) { Text("导入") }
            },
            dismissButton = {
                TextButton(onClick = { confirmImport = false }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun PreviewCard(preview: BackupPreviewDto) {
    val summary = preview.summary
    SectionCard(title = "预览") {
        Text(
            text = "角色 ${summary.assistantCount} · 会话 ${summary.conversationCount} · " +
                "分支节点 ${summary.messageNodeCount} · 消息版本 ${summary.totalMessageVersions} · " +
                "记忆 ${summary.memoryCount}",
            style = MaterialTheme.typography.bodyMedium,
        )
        if (preview.assistantsSample.isNotEmpty()) {
            Text(
                text = "角色示例：${preview.assistantsSample.joinToString("、")}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (preview.conversationsSample.isNotEmpty()) {
            Text(
                text = "会话示例：${preview.conversationsSample.joinToString("、")}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (summary.unsupportedFeatures.isNotEmpty()) {
            Text(
                text = "不支持的特性（会被跳过）：${summary.unsupportedFeatures.joinToString("、")}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )
            content()
        }
    }
}
