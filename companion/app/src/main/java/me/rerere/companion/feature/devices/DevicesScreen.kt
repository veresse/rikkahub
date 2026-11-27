package me.rerere.companion.feature.devices

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.api.dto.DeviceDto

/**
 * 设备管理页。
 *
 * 只消费 [DevicesUiState]；所有写操作通过回调交给 `DevicesViewModel`，
 * 页面本身不做网络门控（离线时按钮已置灰，ViewModel 还会再挡一次）。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DevicesScreen(
    state: DevicesUiState,
    online: Boolean,
    onRefresh: () -> Unit,
    onCreatePairCode: () -> Unit,
    onDismissPairCode: () -> Unit,
    onRename: (deviceId: String, name: String) -> Unit,
    onStartBind: (deviceId: String) -> Unit,
    onSetBindingAssistant: (String?) -> Unit,
    onSetBindingConversation: (String?) -> Unit,
    onBind: () -> Unit,
    onDismissBinding: () -> Unit,
    onRevoke: (deviceId: String) -> Unit,
    onOpenRemote: (deviceId: String) -> Unit,
    onDismissSnackbar: () -> Unit,
    onBack: () -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(state.snackbar) {
        val message = state.snackbar ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(message)
        onDismissSnackbar()
    }

    var renameTarget by remember { mutableStateOf<DeviceDto?>(null) }
    var revokeTarget by remember { mutableStateOf<DeviceDto?>(null) }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("设备") },
                    navigationIcon = {
                        TextButton(onClick = onBack) { Text("返回") }
                    },
                    actions = {
                        TextButton(onClick = onCreatePairCode, enabled = online) {
                            Text("配对码")
                        }
                        TextButton(onClick = onRefresh, enabled = online && !state.loading) {
                            Text("刷新")
                        }
                    },
                )
                if (!online) {
                    OfflineBanner("当前离线，刷新 / 配对 / 改名 / 绑定 / 撤销已禁用")
                }
            }
        },
        floatingActionButton = {
            if (online) {
                ExtendedFloatingActionButton(
                    onClick = onCreatePairCode,
                    content = { Text("添加设备") },
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
                state.loading && state.devices.isEmpty() -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }

                state.error != null && state.devices.isEmpty() -> {
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
                        Button(onClick = onRefresh) { Text("重试") }
                    }
                }

                state.devices.isEmpty() -> {
                    Column(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("还没有设备", style = MaterialTheme.typography.titleMedium)
                        Text(
                            text = "先生成配对码，再在机器人配网页面输入它。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            textAlign = TextAlign.Center,
                        )
                        Button(onClick = onCreatePairCode, enabled = online) {
                            Text("生成配对码")
                        }
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
                        items(state.devices, key = { it.device.id }) { row ->
                            DeviceCard(
                                row = row,
                                assistantName = row.device.boundAssistantId
                                    ?.let { state.assistantNames[it] },
                                busy = state.busyId == row.device.id,
                                online = online,
                                onOpenRemote = { onOpenRemote(row.device.id) },
                                onRename = { renameTarget = row.device },
                                onBind = { onStartBind(row.device.id) },
                                onRevoke = { revokeTarget = row.device },
                            )
                        }
                    }
                }
            }
        }
    }

    state.pairCode?.let { pair ->
        PairCodeDialog(
            pair = pair,
            busy = state.pairCreating,
            onRegenerate = onCreatePairCode,
            onDismiss = onDismissPairCode,
        )
    }

    renameTarget?.let { target ->
        RenameDialog(
            device = target,
            onConfirm = { name ->
                renameTarget = null
                onRename(target.id, name)
            },
            onDismiss = { renameTarget = null },
        )
    }

    revokeTarget?.let { target ->
        AlertDialog(
            onDismissRequest = { revokeTarget = null },
            title = { Text("撤销设备") },
            text = {
                Text("“${target.name}”会立即断开，并需要恢复出厂设置后重新配对。确定撤销吗？")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        revokeTarget = null
                        onRevoke(target.id)
                    },
                    enabled = online,
                ) {
                    Text("撤销", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { revokeTarget = null }) { Text("取消") }
            },
        )
    }

    state.binding?.let { binding ->
        BindDialog(
            binding = binding,
            online = online,
            busy = state.busyId == binding.deviceId,
            onSelectAssistant = onSetBindingAssistant,
            onSelectConversation = onSetBindingConversation,
            onBind = onBind,
            onDismiss = onDismissBinding,
        )
    }
}

@Composable
private fun OfflineBanner(text: String) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.errorContainer,
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
    }
}

@Composable
private fun DeviceCard(
    row: DeviceRow,
    assistantName: String?,
    busy: Boolean,
    online: Boolean,
    onOpenRemote: () -> Unit,
    onRename: () -> Unit,
    onBind: () -> Unit,
    onRevoke: () -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }
    val device = row.device

    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = !busy, onClick = onOpenRemote),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Surface(
                modifier = Modifier.size(44.dp),
                shape = CircleShape,
                color = if (row.online) {
                    MaterialTheme.colorScheme.primaryContainer
                } else {
                    MaterialTheme.colorScheme.surfaceContainerHighest
                },
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(
                        text = if (row.online) "在" else "离",
                        style = MaterialTheme.typography.titleMedium,
                        color = if (row.online) {
                            MaterialTheme.colorScheme.onPrimaryContainer
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
            }

            Column(modifier = Modifier.weight(1f)) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = device.name.ifBlank { "未命名设备" },
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    Tag(
                        text = if (row.online) "在线" else "离线",
                        highlight = row.online,
                    )
                }
                Text(
                    text = buildString {
                        append(device.state.label())
                        append(" · v")
                        append(device.firmwareVersion.ifBlank { "?" })
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = when {
                        !assistantName.isNullOrBlank() -> "绑定角色：$assistantName"
                        // 角色列表加载失败时 `assistantName` 未知，但绑定 ID 还在：
                        // 说“未绑定”会误导用户去重新绑定。
                        device.boundAssistantId != null -> "已绑定角色（名称未知）"
                        else -> "未绑定角色"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!row.online && row.reason.isNotBlank()) {
                    Text(
                        text = row.reason.label(),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            Box {
                TextButton(onClick = { menuOpen = true }, enabled = !busy) {
                    Text(if (busy) "处理中…" else "操作")
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    DropdownMenuItem(
                        text = { Text("遥控") },
                        onClick = {
                            menuOpen = false
                            onOpenRemote()
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("重命名") },
                        enabled = online,
                        onClick = {
                            menuOpen = false
                            onRename()
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("绑定") },
                        enabled = online,
                        onClick = {
                            menuOpen = false
                            onBind()
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("撤销") },
                        enabled = online,
                        onClick = {
                            menuOpen = false
                            onRevoke()
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun PairCodeDialog(
    pair: PairCodeUi,
    busy: Boolean,
    onRegenerate: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("设备配对码") },
        text = {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = pair.code,
                    style = MaterialTheme.typography.displaySmall,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 8.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    text = if (pair.expired) {
                        "已过期，请重新生成"
                    } else {
                        "剩余 ${pair.remainingSeconds / 60}:${(pair.remainingSeconds % 60).toString().padStart(2, '0')}"
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (pair.expired) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
                Text(
                    text = "在机器人配网页面输入该配对码完成绑定。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onRegenerate, enabled = !busy) { Text("重新生成") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("关闭") }
        },
    )
}

@Composable
private fun RenameDialog(
    device: DeviceDto,
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var name by remember(device.id) { mutableStateOf(device.name) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("重命名设备") },
        text = {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("设备名称") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(name) },
                enabled = name.isNotBlank(),
            ) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}

@Composable
private fun BindDialog(
    binding: BindingUi,
    online: Boolean,
    busy: Boolean,
    onSelectAssistant: (String?) -> Unit,
    onSelectConversation: (String?) -> Unit,
    onBind: () -> Unit,
    onDismiss: () -> Unit,
) {
    var assistantMenu by remember { mutableStateOf(false) }
    var conversationMenu by remember { mutableStateOf(false) }
    val assistantName = binding.assistants.firstOrNull { it.id == binding.assistantId }?.name
    val conversationTitle =
        binding.conversations.firstOrNull { it.id == binding.conversationId }?.title

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("绑定“${binding.deviceName}”") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    text = "语音对话会进入所选角色的会话。只选角色则使用该角色的默认会话。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (binding.loading) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp))
                        Text("加载中…", style = MaterialTheme.typography.bodySmall)
                    }
                } else {
                    Box {
                        OptionField(
                            label = "角色",
                            value = assistantName ?: "不绑定",
                            onClick = { assistantMenu = true },
                        )
                        DropdownMenu(
                            expanded = assistantMenu,
                            onDismissRequest = { assistantMenu = false },
                        ) {
                            DropdownMenuItem(
                                text = { Text("不绑定") },
                                onClick = {
                                    assistantMenu = false
                                    onSelectAssistant(null)
                                },
                            )
                            binding.assistants.forEach { assistant: AssistantDto ->
                                DropdownMenuItem(
                                    text = { Text(assistant.name) },
                                    onClick = {
                                        assistantMenu = false
                                        onSelectAssistant(assistant.id)
                                    },
                                )
                            }
                        }
                    }

                    Box {
                        OptionField(
                            label = "会话",
                            value = conversationTitle ?: "默认会话",
                            enabled = binding.assistantId != null,
                            onClick = { conversationMenu = true },
                        )
                        DropdownMenu(
                            expanded = conversationMenu,
                            onDismissRequest = { conversationMenu = false },
                        ) {
                            DropdownMenuItem(
                                text = { Text("默认会话") },
                                onClick = {
                                    conversationMenu = false
                                    onSelectConversation(null)
                                },
                            )
                            binding.conversations.forEach { conversation: ConversationDto ->
                                DropdownMenuItem(
                                    text = { Text(conversation.title.ifBlank { "未命名会话" }) },
                                    onClick = {
                                        conversationMenu = false
                                        onSelectConversation(conversation.id)
                                    },
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onBind,
                enabled = online && !binding.loading && !busy,
            ) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}

@Composable
private fun OptionField(
    label: String,
    value: String,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surfaceContainerHighest,
    ) {
        Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = value,
                style = MaterialTheme.typography.bodyMedium,
                color = if (enabled) {
                    MaterialTheme.colorScheme.onSurface
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
        }
    }
}

/** 纯展示标签；`highlight` 只改配色，不改变可点击性。 */
@Composable
private fun Tag(text: String, highlight: Boolean) {
    Surface(
        shape = MaterialTheme.shapes.small,
        color = if (highlight) {
            MaterialTheme.colorScheme.primaryContainer
        } else {
            MaterialTheme.colorScheme.surfaceContainerHighest
        },
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelSmall,
            color = if (highlight) {
                MaterialTheme.colorScheme.onPrimaryContainer
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
    }
}

private fun String.label(): String = when (this) {
    "idle" -> "待机"
    "listening" -> "聆听中"
    "thinking" -> "思考中"
    "speaking" -> "说话中"
    "acting" -> "动作中"
    "error" -> "异常"
    "device_revoked" -> "设备已撤销"
    "device_offline" -> "设备不在线"
    "app_disconnected" -> "App 连接已断开"
    /** 固件上报了新状态而 App 还不认识：原样显示，别谎称“待机”。 */
    else -> if (isBlank()) "状态未知" else this
}
