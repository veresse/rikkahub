package me.rerere.companion.feature.remote

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt
import kotlinx.coroutines.delay
import me.rerere.companion.data.api.dto.CatalogActionDto
import me.rerere.companion.data.ws.ConnectionState

/** 结果条自动消失时间。 */
private const val RESULT_AUTO_DISMISS_MILLIS = 6_000L

/** 动作参数/状态的展示顺序；未知类别追加在其后。 */
private val CATEGORY_ORDER = listOf("basic", "social", "emotion", "mimic")

/**
 * 遥控可用性门控：App 与服务端的 WS 已连接、目标设备在线、手机有网。
 *
 * 目录是否加载成功**不参与**判断：急停与滑条必须始终可用，只有「重试/重新加载」
 * 按钮额外受门控。
 */
fun canUseRemote(
    connection: ConnectionState,
    deviceOnline: Boolean,
    networkOnline: Boolean,
): Boolean = connection == ConnectionState.Connected && deviceOnline && networkOnline

/**
 * 动作目录来自服务端 REST，与设备是否在线无关：只要 App WS 连接可用且手机有网就允许重试。
 *
 * 用 `deviceOnline` 门控会让「设备离线 → 顺手重拉目录」变成不可能操作。
 */
fun canRefreshCatalog(connection: ConnectionState, networkOnline: Boolean): Boolean =
    connection == ConnectionState.Connected && networkOnline

/**
 * 遥控页。
 *
 * 只消费 [RemoteUiState]；离线时所有控件置灰并显示原因，命令门控在 ViewModel
 * 里还会再挡一次。结果条只反映 `action.result`，`remote.ack` 只显示“已受理”。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun RemoteScreen(
    state: RemoteUiState,
    networkOnline: Boolean,
    onBack: () -> Unit,
    onVolumeChange: (Int) -> Unit,
    onVolumeCommit: () -> Unit,
    onToggleMute: () -> Unit,
    onPanTiltChange: (pan: Double, tilt: Double) -> Unit,
    onPanTiltCommit: () -> Unit,
    onPlayAction: (String) -> Unit,
    onStop: () -> Unit,
    onToggleIdle: () -> Unit,
    onSetIntensity: (Int) -> Unit,
    onSetSpeed: (Int) -> Unit,
    onSetRepeat: (Int) -> Unit,
    onPreviewCalibration: (panOffset: Double, tiltOffset: Double) -> Unit,
    onCommitCalibrationPreview: () -> Unit,
    onCancelCalibration: () -> Unit,
    onSaveCalibration: (panOffset: Double, tiltOffset: Double) -> Unit,
    onRefreshCatalog: () -> Unit,
    onDismissResult: () -> Unit,
) {
    // 急停与滑条共用同一门控，只受连接/在线/网络影响；目录加载失败也照样能停。
    val usable = canUseRemote(state.connection, state.deviceOnline, networkOnline)
    var showCalibration by remember { mutableStateOf(false) }

    LaunchedEffect(state.result) {
        if (state.result == null) return@LaunchedEffect
        delay(RESULT_AUTO_DISMISS_MILLIS)
        onDismissResult()
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
                            Text(state.deviceName.ifBlank { "遥控" })
                            Text(
                                text = state.deviceState.label(),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    },
                    navigationIcon = {
                        // 有可见动作在途时返回会被拦（见 `goBack`）：按钮同步置灰，
                        // 免得点了没反应像是卡死。
                        TextButton(onClick = onBack, enabled = state.sendingAction == null) {
                            Text("返回")
                        }
                    },
                    actions = {
                        Tag(
                            text = if (state.deviceOnline) "在线" else "离线",
                            highlight = state.deviceOnline,
                        )
                    },
                )
                val banner = offlineBannerText(state, networkOnline)
                if (banner != null) OfflineBanner(banner)
            }
        },
        bottomBar = {
            state.result?.let { result ->
                ResultBar(result = result, onDismiss = onDismissResult)
            }
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                StatusCard(state = state, usable = usable, networkOnline = networkOnline)
            }

            item {
                SectionCard(title = "音量") {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Slider(
                            value = state.volume.toFloat(),
                            onValueChange = { onVolumeChange(it.roundToInt()) },
                            valueRange = state.limits.volume.min.toFloat()..state.limits.volume.max.toFloat(),
                            enabled = usable,
                            onValueChangeFinished = onVolumeCommit,
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            text = "${state.volume}",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(start = 8.dp),
                        )
                        TextButton(onClick = onToggleMute, enabled = usable) {
                            Text(if (state.volume == state.limits.volume.muted) "取消静音" else "静音")
                        }
                    }
                    Text(
                        text = "静音只影响机器人说话，不影响它听你说话。拖动停止 200ms 或松手下发。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            item {
                Button(
                    onClick = onStop,
                    enabled = usable,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                        contentColor = MaterialTheme.colorScheme.onError,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("急停归位")
                }
            }

            item {
                ActionSection(
                    state = state,
                    usable = usable,
                    catalogRetryEnabled = canRefreshCatalog(state.connection, networkOnline),
                    onPlayAction = onPlayAction,
                    onSetIntensity = onSetIntensity,
                    onSetSpeed = onSetSpeed,
                    onSetRepeat = onSetRepeat,
                    onRefreshCatalog = onRefreshCatalog,
                )
            }

            item {
                SectionCard(title = "云台角度") {
                    AxisSlider(
                        label = "左右 pan",
                        value = state.pan,
                        min = state.limits.pan.min,
                        max = state.limits.pan.max,
                        enabled = usable,
                        onChange = { onPanTiltChange(it, state.tilt) },
                        onCommit = onPanTiltCommit,
                    )
                    AxisSlider(
                        label = "上下 tilt",
                        value = state.tilt,
                        min = state.limits.tilt.min,
                        max = state.limits.tilt.max,
                        enabled = usable,
                        onChange = { onPanTiltChange(state.pan, it) },
                        onCommit = onPanTiltCommit,
                    )
                    Text(
                        text = "拖动中只更新本地，松手才下发；连续拖动由服务端覆盖旧命令。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            item {
                SectionCard(title = "待机小动作") {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = if (state.idleEnabled) "已开启：待机时会做小动作" else "已关闭",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Switch(
                            checked = state.idleEnabled,
                            onCheckedChange = { onToggleIdle() },
                            enabled = usable,
                        )
                    }
                }
            }

            item {
                SectionCard(title = "校准") {
                    Text(
                        text = "如果机器人回中时朝向偏了，可以在这里调整偏移（±30°）并写入机器人。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedButton(
                        onClick = { showCalibration = true },
                        enabled = usable,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("打开校准")
                    }
                }
            }
        }
    }

    if (showCalibration) {
        CalibrationDialog(
            panOffset = state.offsetPan,
            tiltOffset = state.offsetTilt,
            enabled = usable,
            onPreview = onPreviewCalibration,
            onCommitPreview = onCommitCalibrationPreview,
            onSave = onSaveCalibration,
            onSaved = { showCalibration = false },
            onDismiss = {
                showCalibration = false
                onCancelCalibration()
            },
        )
    }
}

private fun offlineBannerText(state: RemoteUiState, networkOnline: Boolean): String? = when {
    !networkOnline -> "当前离线，遥控已禁用"
    state.connection != ConnectionState.Connected -> "App 与服务端连接已断开，遥控已禁用"
    !state.deviceOnline -> state.offlineReason.ifBlank { "设备已离线，遥控已禁用" }
    else -> null
}

@Composable
private fun StatusCard(state: RemoteUiState, usable: Boolean, networkOnline: Boolean) {
    SectionCard(title = "状态") {
        Text(
            text = buildString {
                append(state.connection.label())
                if (state.sendingAction != null) {
                    append(" · 执行中：")
                    append(state.sendingAction)
                }
            },
            style = MaterialTheme.typography.bodyMedium,
        )
        if (!usable) {
            Text(
                text = offlineBannerText(state, networkOnline) ?: "遥控暂不可用",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun ActionSection(
    state: RemoteUiState,
    usable: Boolean,
    catalogRetryEnabled: Boolean,
    onPlayAction: (String) -> Unit,
    onSetIntensity: (Int) -> Unit,
    onSetSpeed: (Int) -> Unit,
    onSetRepeat: (Int) -> Unit,
    onRefreshCatalog: () -> Unit,
) {
    SectionCard(title = "动作") {
        if (state.catalogLoading && state.actions.isEmpty()) {
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@SectionCard
        }

        if (state.catalogError != null) {
            Text(
                text = state.catalogError,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
            OutlinedButton(
                onClick = onRefreshCatalog,
                enabled = catalogRetryEnabled,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("重试")
            }
            // 已有目录时错误只做提示：动作仍然可以点（catalogError 只说明这次刷新失败）。
            if (state.actions.isEmpty()) return@SectionCard
        }

        val actionsEnabled = usable
        if (state.actions.isEmpty()) {
            Text(
                text = "动作目录为空",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = onRefreshCatalog,
                enabled = catalogRetryEnabled,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("重新加载")
            }
            return@SectionCard
        }

        if (state.actions.any { it.supports.intensity }) {
            ParamRow(label = "强度", value = state.intensity, range = 1..3, enabled = usable) {
                onSetIntensity(it)
            }
        }
        if (state.actions.any { it.supports.speed }) {
            ParamRow(label = "速度", value = state.speed, range = 1..3, enabled = usable) {
                onSetSpeed(it)
            }
        }
        if (state.actions.any { it.supports.repeat }) {
            ParamRow(label = "重复", value = state.repeat, range = 1..5, enabled = usable) {
                onSetRepeat(it)
            }
        }

        val grouped = groupActions(state.actions, state.categories)
        grouped.forEach { (category, actions) ->
            HorizontalDivider()
            Text(
                text = state.categories[category] ?: category,
                style = MaterialTheme.typography.titleSmall,
            )
            FlowRow(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                actions.forEach { action ->
                    FilledTonalButton(
                        onClick = { onPlayAction(action.name) },
                        enabled = actionsEnabled && state.sendingAction == null,
                    ) {
                        Text(action.labelZh.ifBlank { action.name })
                    }
                }
            }
        }
    }
}

/** 按固定顺序分组；未知类别追加在末尾，未知商品也不丢弃。 */
private fun groupActions(
    actions: List<CatalogActionDto>,
    categories: Map<String, String>,
): List<Pair<String, List<CatalogActionDto>>> {
    val known = CATEGORY_ORDER + categories.keys.filterNot { it in CATEGORY_ORDER }
    val order = (known + actions.map { it.category }.filterNot { it in known }).distinct()
    return order.mapNotNull { category ->
        val inCategory = actions.filter { it.category == category }
        inCategory.takeIf { it.isNotEmpty() }?.let { category to it }
    }
}

@Composable
private fun ParamRow(
    label: String,
    value: Int,
    range: IntRange,
    enabled: Boolean,
    onSelect: (Int) -> Unit,
) {
    Text(text = label, style = MaterialTheme.typography.bodyMedium)
    val values = range.toList()
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        values.forEachIndexed { index, item ->
            SegmentedButton(
                selected = value == item,
                onClick = { onSelect(item) },
                enabled = enabled,
                shape = SegmentedButtonDefaults.itemShape(index, values.size),
            ) {
                Text("$item")
            }
        }
    }
}

@Composable
private fun AxisSlider(
    label: String,
    value: Double,
    min: Double,
    max: Double,
    enabled: Boolean,
    onChange: (Double) -> Unit,
    onCommit: () -> Unit,
) {
    Text(
        text = "$label：${"%.1f".format(value)}°",
        style = MaterialTheme.typography.bodyMedium,
    )
    Slider(
        value = value.toFloat(),
        onValueChange = { onChange(it.toDouble()) },
        valueRange = min.toFloat()..max.toFloat(),
        enabled = enabled,
        onValueChangeFinished = onCommit,
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun SectionCard(title: String, content: @Composable () -> Unit) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(text = title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun CalibrationDialog(
    panOffset: Double,
    tiltOffset: Double,
    enabled: Boolean,
    onPreview: (Double, Double) -> Unit,
    onCommitPreview: () -> Unit,
    onSave: (Double, Double) -> Unit,
    onSaved: () -> Unit,
    onDismiss: () -> Unit,
) {
    var confirmSave by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("云台校准") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = "拖动会立即预览；保存后写入机器人，重启仍生效。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                AxisSlider(
                    label = "左右偏移",
                    value = panOffset,
                    min = -REMOTE_OFFSET_LIMIT,
                    max = REMOTE_OFFSET_LIMIT,
                    enabled = enabled,
                    onChange = { onPreview(it, tiltOffset) },
                    onCommit = onCommitPreview,
                )
                AxisSlider(
                    label = "上下偏移",
                    value = tiltOffset,
                    min = -REMOTE_OFFSET_LIMIT,
                    max = REMOTE_OFFSET_LIMIT,
                    enabled = enabled,
                    onChange = { onPreview(panOffset, it) },
                    onCommit = onCommitPreview,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { confirmSave = true }, enabled = enabled) { Text("保存") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )

    if (confirmSave) {
        AlertDialog(
            onDismissRequest = { confirmSave = false },
            title = { Text("写入机器人？") },
            text = {
                Text(
                    "偏移 左右 ${"%.1f".format(panOffset)}° / 上下 ${"%.1f".format(tiltOffset)}° " +
                        "会写入机器人 NVS。确定保存吗？",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmSave = false
                        onSave(panOffset, tiltOffset)
                        // 保存后不能走 onDismiss：那会取消校准并预览旧偏移，
                        // 把刚写入的值在运行时又盖回去。
                        onSaved()
                    },
                    enabled = enabled,
                ) { Text("保存") }
            },
            dismissButton = {
                TextButton(onClick = { confirmSave = false }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun ResultBar(result: RemoteResult, onDismiss: () -> Unit) {
    val container = when (result.kind) {
        RemoteResultKind.INFO -> MaterialTheme.colorScheme.surfaceContainerHighest
        RemoteResultKind.DONE -> MaterialTheme.colorScheme.primaryContainer
        RemoteResultKind.FAILED -> MaterialTheme.colorScheme.errorContainer
        RemoteResultKind.TIMEOUT -> MaterialTheme.colorScheme.tertiaryContainer
    }
    val onContainer = when (result.kind) {
        RemoteResultKind.INFO -> MaterialTheme.colorScheme.onSurfaceVariant
        RemoteResultKind.DONE -> MaterialTheme.colorScheme.onPrimaryContainer
        RemoteResultKind.FAILED -> MaterialTheme.colorScheme.onErrorContainer
        RemoteResultKind.TIMEOUT -> MaterialTheme.colorScheme.onTertiaryContainer
    }
    Surface(modifier = Modifier.fillMaxWidth(), color = container) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = result.text,
                style = MaterialTheme.typography.bodyMedium,
                color = onContainer,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onDismiss) { Text("知道了") }
        }
    }
}

@Composable
private fun OfflineBanner(text: String) {
    Surface(modifier = Modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.errorContainer) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onErrorContainer,
            textAlign = TextAlign.Start,
        )
    }
}

/** 纯展示标签。 */
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

private fun ConnectionState.label(): String = when (this) {
    ConnectionState.Stopped -> "未连接"
    ConnectionState.Connecting -> "连接中…"
    ConnectionState.Connected -> "已连接"
    is ConnectionState.Retrying -> "重连中（第 $attempt 次）"
}

private fun String.label(): String = when (this) {
    "" -> "状态未知"
    "idle" -> "待机"
    "listening" -> "聆听中"
    "thinking" -> "思考中"
    "speaking" -> "说话中"
    "acting" -> "动作中"
    "error" -> "异常"
    else -> this
}
