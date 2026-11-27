package me.rerere.companion.feature.assistants

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.SharedFlow

/**
 * 角色编辑页。
 *
 * 字段全部通过 [onUpdate] 写回 ViewModel；页面不做校验，只有保存按钮的
 * 可用状态与提示来自 [AssistantEditUiState]。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AssistantEditScreen(
    state: AssistantEditUiState,
    events: SharedFlow<AssistantEditEvent>,
    online: Boolean,
    onUpdate: ((AssistantEditUiState) -> AssistantEditUiState) -> Unit,
    onSave: () -> Unit,
    onRetryLoad: () -> Unit,
    onBack: () -> Unit,
) {
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(events) {
        events.collect { event ->
            if (event is AssistantEditEvent.Message) {
                snackbarHostState.showSnackbar(event.text)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (state.name.isBlank()) "新建角色" else state.name) },
                navigationIcon = {
                    TextButton(onClick = onBack, enabled = !state.saving) { Text("返回") }
                },
                actions = {
                    TextButton(
                        onClick = onSave,
                        enabled = online && !state.saving && !state.loading && !state.loadFailed,
                    ) {
                        Text(if (state.saving) "保存中…" else "保存")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        if (state.loading) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        if (state.loadFailed) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    text = state.error ?: "加载角色失败",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyLarge,
                )
                Button(onClick = onRetryLoad) { Text("重试") }
                TextButton(onClick = onBack) { Text("返回") }
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (!online) {
                Text(
                    text = "当前离线，保存已禁用",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            OutlinedTextField(
                value = state.name,
                onValueChange = { value -> onUpdate { it.copy(name = value) } },
                label = { Text("名称") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.modelId,
                onValueChange = { value -> onUpdate { it.copy(modelId = value) } },
                label = { Text("模型 ID") },
                supportingText = { Text("留空表示使用服务端默认模型") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.systemPrompt,
                onValueChange = { value -> onUpdate { it.copy(systemPrompt = value) } },
                label = { Text("人设 / 系统提示词") },
                minLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = state.contextMessageLimit,
                    onValueChange = { value ->
                        onUpdate { it.copy(contextMessageLimit = value.filter(Char::isDigit)) }
                    },
                    label = { Text("上下文条数") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = state.temperature,
                    onValueChange = { value -> onUpdate { it.copy(temperature = value) } },
                    label = { Text("温度") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = state.topP,
                    onValueChange = { value -> onUpdate { it.copy(topP = value) } },
                    label = { Text("top_p") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
            }

            HorizontalDivider()

            Text("搜索", style = MaterialTheme.typography.titleSmall)
            SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                SEARCH_MODES.forEachIndexed { index, mode ->
                    SegmentedButton(
                        selected = state.webSearchMode == mode,
                        onClick = { onUpdate { it.copy(webSearchMode = mode) } },
                        shape = SegmentedButtonDefaults.itemShape(index, SEARCH_MODES.size),
                    ) {
                        Text(mode.label())
                    }
                }
            }

            HorizontalDivider()

            SwitchRow(
                title = "角色记忆",
                checked = state.enableMemory,
                onCheckedChange = { value -> onUpdate { it.copy(enableMemory = value) } },
            )
            SwitchRow(
                title = "使用全局记忆",
                checked = state.useGlobalMemory,
                onCheckedChange = { value -> onUpdate { it.copy(useGlobalMemory = value) } },
            )
            SwitchRow(
                title = "动作联动",
                checked = state.enableActionSync,
                onCheckedChange = { value -> onUpdate { it.copy(enableActionSync = value) } },
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "自动总结",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "未实现",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(checked = false, onCheckedChange = null, enabled = false)
            }

            state.error?.let { message ->
                Text(
                    text = message,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }

            Button(
                onClick = onSave,
                enabled = online && !state.saving,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (state.saving) "保存中…" else "保存")
            }
        }
    }
}

@Composable
private fun SwitchRow(
    title: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text = title, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

private val SEARCH_MODES = listOf("off", "auto", "on")

private fun String.label(): String = when (this) {
    "on" -> "总是"
    "auto" -> "自动"
    else -> "关闭"
}
