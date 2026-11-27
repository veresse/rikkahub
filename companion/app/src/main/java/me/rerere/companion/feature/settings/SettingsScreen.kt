package me.rerere.companion.feature.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.flow.SharedFlow
import me.rerere.companion.data.api.dto.AuthTokenDto

/**
 * 设置页写操作门控：手机有网且没有在途的保存/撤销。
 *
 * 和 4C 的遥控门控一致，离线禁用只是第一道；ViewModel 里还会再挡一次。
 */
fun settingsActionsEnabled(online: Boolean, busy: Boolean): Boolean = online && !busy

private const val KEY_HELP = "Key 只写不回显：保存后服务端只告诉你“已配置”，不会返回明文或片段。"

private val VOICE_PROVIDERS = listOf(
    "unavailable" to "关闭（不可用）",
    "dashscope" to "DashScope",
)

/**
 * 设置页：运行时可改的模型/语音/搜索配置、只写 Key 与登录设备管理。
 *
 * 页面不感知 `AppContainer`；保存、清除、撤销与退出都由外部回调注入。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    state: SettingsUiState,
    events: SharedFlow<SettingsEvent>,
    online: Boolean,
    currentTokenId: String?,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onLogout: () -> Unit,
    onDeepseekBaseUrlChange: (String) -> Unit,
    onDeepseekModelChange: (String) -> Unit,
    onDeepseekKeyDraftChange: (String) -> Unit,
    onSaveDeepseek: () -> Unit,
    onClearDeepseekKey: () -> Unit,
    onDashscopeKeyDraftChange: (String) -> Unit,
    onDashscopeRegionChange: (String) -> Unit,
    onDashscopeAsrModelChange: (String) -> Unit,
    onDashscopeTtsModelChange: (String) -> Unit,
    onDashscopeTtsVoiceChange: (String) -> Unit,
    onVoiceEnabledChange: (Boolean) -> Unit,
    onVoiceProviderChange: (String) -> Unit,
    onSaveVoice: () -> Unit,
    onClearDashscopeKey: () -> Unit,
    onSearchEnabledChange: (Boolean) -> Unit,
    onOpenBackup: () -> Unit,
    onOpenAssets: () -> Unit,
    onRefreshTokens: () -> Unit,
    onRevokeToken: (String) -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(events) {
        events.collect { event ->
            if (event is SettingsEvent.Message) {
                snackbarHostState.showSnackbar(event.text)
            }
        }
    }

    val busy = state.saving || state.revokingId != null
    val actionsEnabled = settingsActionsEnabled(online, busy)

    var confirmClearDeepseek by remember { mutableStateOf(false) }
    var confirmClearDashscope by remember { mutableStateOf(false) }
    var confirmLogout by remember { mutableStateOf(false) }
    var revokeTarget by remember { mutableStateOf<AuthTokenDto?>(null) }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("设置") },
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !busy) { Text("返回") }
                    },
                    actions = {
                        TextButton(
                            onClick = onRefresh,
                            enabled = online && !state.loading && !state.saving,
                        ) { Text("刷新") }
                    },
                )
                if (!online) {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        color = MaterialTheme.colorScheme.errorContainer,
                    ) {
                        Text(
                            text = "当前离线，保存 / 清除 / 撤销已禁用",
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                        )
                    }
                }
                if (state.loading || state.saving) {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            state.error?.let { message ->
                item { ErrorCard(message = message, onRetry = onRefresh, enabled = online) }
            }

            item {
                SectionCard(title = "对话模型（DeepSeek）") {
                    Text(
                        text = KEY_HELP,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    SecretStatus(
                        configured = state.deepseekConfigured,
                        source = state.deepseekSource,
                    )
                    OutlinedTextField(
                        value = state.deepseekBaseUrl,
                        onValueChange = onDeepseekBaseUrlChange,
                        label = { Text("服务地址") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.deepseekModel,
                        onValueChange = onDeepseekModelChange,
                        label = { Text("默认模型") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.deepseekKeyDraft,
                        onValueChange = onDeepseekKeyDraftChange,
                        label = { Text("API Key（留空表示不修改）") },
                        singleLine = true,
                        enabled = !state.saving,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    ActionRow {
                        Button(onClick = onSaveDeepseek, enabled = actionsEnabled) {
                            Text(if (state.saving) "保存中…" else "保存")
                        }
                        if (state.deepseekConfigured) {
                            OutlinedButton(
                                onClick = { confirmClearDeepseek = true },
                                enabled = actionsEnabled,
                            ) { Text("清除 Key") }
                        }
                    }
                }
            }

            item {
                SectionCard(title = "语音（DashScope）") {
                    Text(
                        text = KEY_HELP,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    SecretStatus(
                        configured = state.dashscopeConfigured,
                        source = state.dashscopeSource,
                    )
                    Text(
                        text = voiceSummary(state),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("启用语音")
                            Text(
                                text = "关闭后设备端语音入口立即不可用。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = state.voiceEnabled,
                            onCheckedChange = onVoiceEnabledChange,
                            enabled = actionsEnabled,
                        )
                    }
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                        VOICE_PROVIDERS.forEachIndexed { index, (value, label) ->
                            SegmentedButton(
                                selected = state.voiceProvider == value,
                                onClick = { onVoiceProviderChange(value) },
                                enabled = actionsEnabled,
                                shape = SegmentedButtonDefaults.itemShape(
                                    index = index,
                                    count = VOICE_PROVIDERS.size,
                                ),
                            ) { Text(label) }
                        }
                    }
                    OutlinedTextField(
                        value = state.dashscopeKeyDraft,
                        onValueChange = onDashscopeKeyDraftChange,
                        label = { Text("API Key（留空表示不修改）") },
                        singleLine = true,
                        enabled = !state.saving,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.dashscopeRegion,
                        onValueChange = onDashscopeRegionChange,
                        label = { Text("区域（如 cn-beijing）") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.dashscopeAsrModel,
                        onValueChange = onDashscopeAsrModelChange,
                        label = { Text("ASR 模型") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.dashscopeTtsModel,
                        onValueChange = onDashscopeTtsModelChange,
                        label = { Text("TTS 模型") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = state.dashscopeTtsVoice,
                        onValueChange = onDashscopeTtsVoiceChange,
                        label = { Text("TTS 音色") },
                        singleLine = true,
                        enabled = !state.saving,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    ActionRow {
                        Button(onClick = onSaveVoice, enabled = actionsEnabled) {
                            Text(if (state.saving) "保存中…" else "保存")
                        }
                        if (state.dashscopeConfigured) {
                            OutlinedButton(
                                onClick = { confirmClearDashscope = true },
                                enabled = actionsEnabled,
                            ) { Text("清除 Key") }
                        }
                    }
                }
            }

            item {
                SectionCard(title = "联网搜索") {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("允许联网搜索")
                            Text(
                                text = "关闭后所有角色都不会发起检索，也不会有搜索状态提示。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(
                            checked = state.searchEnabled,
                            onCheckedChange = onSearchEnabledChange,
                            enabled = actionsEnabled,
                        )
                    }
                }
            }

            item {
                SectionCard(title = "备份与恢复") {
                    Text(
                        text = "导出服务器数据，或从备份包导入角色、会话与记忆。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(
                        onClick = onOpenBackup,
                        enabled = !busy,
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("打开备份与恢复") }
                }
            }

            item {
                SectionCard(title = "素材包") {
                    Text(
                        text = "上传 240×240 PNG 序列包，启用或回滚设备素材。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(
                        onClick = onOpenAssets,
                        enabled = !busy,
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("打开素材包管理") }
                }
            }

            item {
                SectionCard(title = "登录设备") {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "撤销后该设备需要重新登录。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = onRefreshTokens, enabled = online && !state.tokensLoading) {
                            Text("刷新列表")
                        }
                    }
                    if (state.tokensLoading && state.tokens.isEmpty()) {
                        CircularProgressIndicator()
                    }
                    state.tokensError?.let { message ->
                        Text(
                            text = "设备列表加载失败：$message",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                    state.tokens.forEach { token ->
                        HorizontalDivider()
                        TokenRow(
                            token = token,
                            current = token.id == currentTokenId,
                            revoking = state.revokingId == token.id,
                            canRevoke = actionsEnabled,
                            onRevoke = { revokeTarget = token },
                        )
                    }
                }
            }

            item {
                OutlinedButton(
                    onClick = { confirmLogout = true },
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("退出登录") }
            }
        }
    }

    if (confirmClearDeepseek) {
        ConfirmDialog(
            title = "清除 DeepSeek Key",
            text = "清除后服务端会回落到环境变量配置（如果有）。",
            confirmText = "清除",
            onConfirm = {
                confirmClearDeepseek = false
                onClearDeepseekKey()
            },
            onDismiss = { confirmClearDeepseek = false },
        )
    }
    if (confirmClearDashscope) {
        ConfirmDialog(
            title = "清除语音 Key",
            text = "清除后语音将回落到环境变量配置或变为不可用。",
            confirmText = "清除",
            onConfirm = {
                confirmClearDashscope = false
                onClearDashscopeKey()
            },
            onDismiss = { confirmClearDashscope = false },
        )
    }
    revokeTarget?.let { target ->
        ConfirmDialog(
            title = "撤销登录",
            text = "确定撤销“${target.deviceName.ifBlank { "未知设备" }}”的登录吗？",
            confirmText = "撤销",
            onConfirm = {
                revokeTarget = null
                onRevokeToken(target.id)
            },
            onDismiss = { revokeTarget = null },
        )
    }
    if (confirmLogout) {
        ConfirmDialog(
            title = "退出登录",
            text = "退出后需要重新输入服务器地址和账号。",
            confirmText = "退出",
            onConfirm = {
                confirmLogout = false
                onLogout()
            },
            onDismiss = { confirmLogout = false },
        )
    }
}

private fun voiceSummary(state: SettingsUiState): String {
    val effective = when (state.voiceEffective) {
        "dashscope" -> "DashScope 生效"
        "mock" -> "Mock（仅测试）"
        else -> "语音不可用"
    }
    val reason = when (state.voiceReason) {
        "voice_disabled" -> "语音已关闭"
        "dashscope_key_missing" -> "缺少 DashScope Key"
        "provider_unavailable" -> "未知提供方"
        else -> null
    }
    return if (reason == null) effective else "$effective · $reason"
}

@Composable
private fun SecretStatus(configured: Boolean, source: String) {
    val label = when {
        !configured -> "未配置"
        source == "database" -> "已配置（保存在服务端）"
        source == "environment" -> "已配置（来自环境变量）"
        else -> "已配置"
    }
    Text(
        text = label,
        style = MaterialTheme.typography.labelLarge,
        color = if (configured) {
            MaterialTheme.colorScheme.primary
        } else {
            MaterialTheme.colorScheme.onSurfaceVariant
        },
    )
}

@Composable
private fun TokenRow(
    token: AuthTokenDto,
    current: Boolean,
    revoking: Boolean,
    canRevoke: Boolean,
    onRevoke: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = token.deviceName.ifBlank { "未知设备" },
                    style = MaterialTheme.typography.titleSmall,
                )
                if (current) {
                    Surface(
                        shape = MaterialTheme.shapes.small,
                        color = MaterialTheme.colorScheme.primaryContainer,
                    ) {
                        Text(
                            text = "当前",
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                        )
                    }
                }
            }
            Text(
                text = "创建于 ${formatMillis(token.createdAt)}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        TextButton(onClick = onRevoke, enabled = canRevoke && !revoking) {
            Text(if (revoking) "撤销中…" else "撤销")
        }
    }
}

private fun formatMillis(millis: Long): String {
    if (millis <= 0L) return "未知时间"
    return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(millis))
}

@Composable
private fun ErrorCard(message: String, onRetry: () -> Unit, enabled: Boolean) {
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
            FilledTonalButton(onClick = onRetry, enabled = enabled) { Text("重试") }
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

@Composable
private fun ActionRow(content: @Composable () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

@Composable
private fun ConfirmDialog(
    title: String,
    text: String,
    confirmText: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(text) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(confirmText, color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}
