package me.rerere.companion.feature.backup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.io.InputStream
import java.io.OutputStream
import java.util.Locale
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.BackupPreviewDto
import me.rerere.companion.data.api.dto.BackupResultDto
import me.rerere.companion.data.repo.BackupRepository

/** 备份页状态；[exportedBytes] 是最近一次成功导出的字节数。 */
data class BackupUiState(
    val previewing: Boolean = false,
    val preview: BackupPreviewDto? = null,
    val importing: Boolean = false,
    val result: BackupResultDto? = null,
    val exporting: Boolean = false,
    val exportedBytes: Long? = null,
    val error: String? = null,
)

sealed interface BackupEvent {
    data class Message(val text: String) : BackupEvent
}

/**
 * 备份导入/导出。
 *
 * 上传/下载都由调用方提供流（`ContentResolver` 打开），ViewModel 不持有
 * URI、不把整包读进内存。所有入口先过“在线 + 空闲”门控。
 */
class BackupViewModel(
    private val repository: BackupRepository,
    private val isOnline: () -> Boolean,
) : ViewModel() {

    private val _state = MutableStateFlow(BackupUiState())
    val state: StateFlow<BackupUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<BackupEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<BackupEvent> = _events.asSharedFlow()

    private val busy: Boolean
        get() = _state.value.run { previewing || importing || exporting }

    fun preview(open: () -> InputStream?) {
        if (!requireIdle()) return
        _state.update { it.copy(previewing = true, error = null, result = null) }

        val stream = runCatching { open() }.getOrNull()
        if (stream == null) {
            _state.update { it.copy(previewing = false, error = "无法读取所选文件") }
            return
        }

        viewModelScope.launch {
            val result = stream.use { repository.preview(it, "backup.zip") }
            when (result) {
                is AppResult.Ok -> _state.update {
                    it.copy(previewing = false, preview = result.value, error = null)
                }

                is AppResult.Err -> _state.update {
                    // 网络问题保留旧预览可重试；服务端拒绝（如已过期）则清掉。
                    val keep = result.error is AppError.Network
                    it.copy(
                        previewing = false,
                        preview = if (keep) it.preview else null,
                        error = result.error.message,
                    )
                }
            }
        }
    }

    fun confirm() {
        if (!requireIdle()) return
        val preview = _state.value.preview
        if (preview == null) {
            _state.update { it.copy(error = "没有可导入的预览，请重新选择文件") }
            return
        }
        _state.update { it.copy(importing = true, error = null) }
        viewModelScope.launch {
            when (val result = repository.confirm(preview.previewId)) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(importing = false, preview = null, result = result.value)
                    }
                    emit(
                        "导入完成：角色 ${result.value.importedAssistants} / " +
                            "会话 ${result.value.importedConversations}",
                    )
                }

                is AppResult.Err -> _state.update {
                    val keep = result.error is AppError.Network
                    it.copy(
                        importing = false,
                        preview = if (keep) it.preview else null,
                        error = result.error.message,
                    )
                }
            }
        }
    }

    fun exportTo(open: () -> OutputStream?) {
        if (!requireIdle()) return
        _state.update { it.copy(exporting = true, error = null, exportedBytes = null) }

        val sink = runCatching { open() }.getOrNull()
        if (sink == null) {
            _state.update { it.copy(exporting = false, error = "无法创建导出文件") }
            return
        }

        viewModelScope.launch {
            val result = sink.use { repository.exportTo(it) }
            when (result) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(exporting = false, exportedBytes = result.value)
                    }
                    emit("已导出 ${formatMb(result.value)}")
                }

                is AppResult.Err -> _state.update {
                    it.copy(exporting = false, error = result.error.message)
                }
            }
        }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    private fun requireIdle(): Boolean {
        if (!isOnline()) {
            _state.update { it.copy(error = "当前离线，无法执行该操作") }
            return false
        }
        if (busy) return false
        return true
    }

    private fun emit(text: String) {
        _events.tryEmit(BackupEvent.Message(text))
    }
}

internal fun formatMb(bytes: Long): String {
    val mb = bytes / (1024.0 * 1024.0)
    return String.format(Locale.US, "%.1f MB", mb)
}
