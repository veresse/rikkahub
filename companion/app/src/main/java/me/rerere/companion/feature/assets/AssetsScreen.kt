package me.rerere.companion.feature.assets

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import android.graphics.BitmapFactory
import me.rerere.companion.data.api.dto.AssetPackageDto
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.feature.backup.formatMb

/**
 * 素材页写操作门控：手机有网且没有在途的上传/确认/回滚。
 */
fun assetActionsEnabled(online: Boolean, busy: Boolean): Boolean = online && !busy

/**
 * 素材包管理：当前生效包、上一版回滚、上传预览与首帧展示。
 *
 * 只做 App 侧管理；设备端下载/热替换属于 Phase 5。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AssetsScreen(
    state: AssetsUiState,
    online: Boolean,
    onPreview: (() -> java.io.InputStream?) -> Unit,
    onConfirm: () -> Unit,
    onRollback: () -> Unit,
    onRefresh: () -> Unit,
    onDismissError: () -> Unit,
    onBack: () -> Unit,
) {
    val actionsEnabled = assetActionsEnabled(online, state.busy)
    var confirmUpload by remember { mutableStateOf(false) }
    var confirmRollback by remember { mutableStateOf(false) }

    val context = LocalContext.current
    val openDocument = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        // 取消选择不是错误：不要用 null 流触发“无法读取所选文件”。
        if (uri != null) {
            onPreview {
                runCatching { context.contentResolver.openInputStream(uri) }.getOrNull()
            }
        }
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("素材包") },
                    navigationIcon = {
                        TextButton(onClick = onBack, enabled = !state.busy) { Text("返回") }
                    },
                    actions = {
                        TextButton(
                            onClick = onRefresh,
                            enabled = online && !state.loading && !state.busy,
                        ) { Text("刷新") }
                    },
                )
                if (!online) {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        color = MaterialTheme.colorScheme.errorContainer,
                    ) {
                        Text(
                            text = "当前离线，上传 / 启用 / 回滚已禁用",
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                        )
                    }
                }
                if (state.loading || state.busy) {
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
                SectionCard(title = "当前素材") {
                    val active = state.active
                    if (active == null) {
                        Text(
                            text = "尚未启用素材包，设备使用内置占位图。",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    } else {
                        PackageSummary(active)
                    }
                    FramePreview(
                        bytes = state.previewBytes,
                        label = if (active == null) "占位图首帧" else "当前包首帧",
                    )
                }
            }

            item {
                SectionCard(title = "上传新素材包") {
                    Text(
                        text = "ZIP 只允许 manifest.json 与 frames/NNN.png；帧必须是 240×240 PNG，" +
                            "单帧 ≤ 512 KiB，帧数 1–240。上传只校验暂存，确认后才生效。",
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
                        Text(if (state.uploading) "校验中…" else "选择素材包 ZIP")
                    }
                }
            }

            state.preview?.let { preview ->
                item { PreviewSummary(preview) }
                item {
                    Button(onClick = { confirmUpload = true }, enabled = actionsEnabled) {
                        Text(if (state.confirming) "启用中…" else "启用此素材包")
                    }
                }
            }

            state.previous?.let { previous ->
                item {
                    SectionCard(title = "上一版（可回滚）") {
                        PackageSummary(previous)
                        OutlinedButton(
                            onClick = { confirmRollback = true },
                            enabled = actionsEnabled,
                        ) { Text(if (state.rollingBack) "回滚中…" else "回滚到这一版") }
                    }
                }
            }

            item {
                SectionCard(title = "内置占位图") {
                    Text(
                        text = "${state.fallback.name} · ${state.fallback.version} · " +
                            "${state.fallback.frameCount} 帧",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        text = "没有可用素材或包损坏时，设备端显示占位图。固件热替换在 Phase 5 提供。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
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

    if (confirmUpload) {
        AlertDialog(
            onDismissRequest = { confirmUpload = false },
            title = { Text("启用素材包") },
            text = { Text("启用后当前版本会降为上一版，可随时回滚。确定继续吗？") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmUpload = false
                        onConfirm()
                    },
                ) { Text("启用") }
            },
            dismissButton = {
                TextButton(onClick = { confirmUpload = false }) { Text("取消") }
            },
        )
    }
    if (confirmRollback) {
        AlertDialog(
            onDismissRequest = { confirmRollback = false },
            title = { Text("回滚素材") },
            text = { Text("回滚会删除当前生效版本，把上一版恢复为当前版本。确定继续吗？") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmRollback = false
                        onRollback()
                    },
                ) { Text("回滚") }
            },
            dismissButton = {
                TextButton(onClick = { confirmRollback = false }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun PackageSummary(pkg: AssetPackageDto) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            text = "${pkg.name} · ${pkg.version}",
            style = MaterialTheme.typography.titleSmall,
        )
        Text(
            text = "${pkg.frameCount} 帧 · ${pkg.fps} fps · ${formatMb(pkg.sizeBytes)}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = "SHA-256 ${pkg.sha256.take(12)}…",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun PreviewSummary(upload: AssetUploadDto) {
    SectionCard(title = "待启用的素材包") {
        Text(
            text = "${upload.name} · ${upload.version}",
            style = MaterialTheme.typography.titleSmall,
        )
        Text(
            text = "${upload.frameCount} 帧 · ${upload.fps} fps · ${formatMb(upload.sizeBytes)}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * 首次帧在组合里解码一次；JVM 单测不覆盖解码，失败时只显示占位文字。
 */
@Composable
private fun FramePreview(bytes: ByteArray?, label: String) {
    val bitmap = remember(bytes) {
        bytes?.let { data ->
            runCatching { BitmapFactory.decodeByteArray(data, 0, data.size) }.getOrNull()
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (bitmap == null) {
            Text(
                text = "$label：预览不可用",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = label,
                modifier = Modifier.size(120.dp),
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
