package me.rerere.companion.feature.assets

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.io.InputStream
import kotlinx.coroutines.Job
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
import me.rerere.companion.data.api.dto.AssetFallbackDto
import me.rerere.companion.data.api.dto.AssetPackageDto
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.data.repo.AssetRepository
import me.rerere.companion.data.repo.MAX_ASSET_FRAME_BYTES

/** 首帧预览上限：服务端单帧已限 512 KiB，这里再兜一层。 */
internal const val MAX_PREVIEW_BYTES = MAX_ASSET_FRAME_BYTES

data class AssetsUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val active: AssetPackageDto? = null,
    val previous: AssetPackageDto? = null,
    val fallback: AssetFallbackDto = AssetFallbackDto(),
    /** 已校验、待确认的上传。 */
    val preview: AssetUploadDto? = null,
    val uploading: Boolean = false,
    val confirming: Boolean = false,
    val rollingBack: Boolean = false,
    /** 当前 active（或占位图）的首帧字节；解码/展示由 Screen 负责。 */
    val previewBytes: ByteArray? = null,
) {
    val busy: Boolean get() = uploading || confirming || rollingBack
}

sealed interface AssetsEvent {
    data class Message(val text: String) : AssetsEvent
}

/**
 * 素材包管理。
 *
 * 上传由调用方提供“打开流”的 lambda，ViewModel 在通过在线/空闲门控后
 * 才真正读取并负责关闭；确认/回滚成功后直接用服务端返回的视图覆盖状态。
 */
class AssetsViewModel(
    private val repository: AssetRepository,
    private val isOnline: () -> Boolean,
) : ViewModel() {

    private val _state = MutableStateFlow(AssetsUiState(loading = true))
    val state: StateFlow<AssetsUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<AssetsEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<AssetsEvent> = _events.asSharedFlow()

    private var previewJob: Job? = null
    private var previewGeneration = 0
    private var loadGeneration = 0

    init {
        load()
    }

    fun load() {
        if (!isOnline()) {
            _state.update { it.copy(loading = false, error = "当前离线，无法加载素材包") }
            return
        }
        // 刷新期间用户可以确认/回滚；慢请求的旧快照不能覆盖新状态。
        loadGeneration += 1
        val generation = loadGeneration
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            val result = repository.load()
            if (generation != loadGeneration) return@launch
            when (result) {
                is AppResult.Ok -> {
                    val activeChanged = _state.value.active?.id != result.value.active?.id
                    if (activeChanged) invalidatePreviewImage()
                    _state.update {
                        it.copy(
                            loading = false,
                            active = result.value.active,
                            previous = result.value.previous,
                            fallback = result.value.fallback,
                            previewBytes = if (activeChanged) null else it.previewBytes,
                            error = null,
                        )
                    }
                }

                is AppResult.Err -> _state.update {
                    it.copy(loading = false, error = result.error.message)
                }
            }
        }
    }

    fun preview(open: () -> InputStream?) {
        if (!requireIdle()) return
        _state.update { it.copy(uploading = true, error = null) }

        val stream = runCatching { open() }.getOrNull()
        if (stream == null) {
            _state.update { it.copy(uploading = false, error = "无法读取所选文件") }
            return
        }

        viewModelScope.launch {
            val result = stream.use { repository.preview(it, "assets.zip") }
            when (result) {
                is AppResult.Ok -> _state.update {
                    it.copy(uploading = false, preview = result.value, error = null)
                }

                is AppResult.Err -> _state.update {
                    it.copy(uploading = false, error = result.error.message)
                }
            }
        }
    }

    fun confirm() {
        if (!requireIdle()) return
        val preview = _state.value.preview
        if (preview == null) {
            _state.update { it.copy(error = "没有可启用的素材包，请重新选择文件") }
            return
        }
        _state.update { it.copy(confirming = true, error = null) }

        viewModelScope.launch {
            when (val result = repository.confirm(preview.uploadId)) {
                is AppResult.Ok -> {
                    invalidatePreviewImage()
                    invalidateLoad()
                    _state.update {
                        it.copy(
                            loading = false,
                            confirming = false,
                            active = result.value.active,
                            previous = result.value.previous,
                            fallback = result.value.fallback,
                            preview = null,
                            previewBytes = null,
                            error = null,
                        )
                    }
                    emit("素材包已启用")
                }

                is AppResult.Err -> _state.update {
                    it.copy(
                        confirming = false,
                        // 过期后旧 preview 已不可用；其他错误保留供重试。
                        preview = if (result.error.isExpiredUpload()) null else it.preview,
                        error = result.error.message,
                    )
                }
            }
        }
    }

    fun rollback() {
        if (!requireIdle()) return
        _state.update { it.copy(rollingBack = true, error = null) }
        viewModelScope.launch {
            when (val result = repository.rollback()) {
                is AppResult.Ok -> {
                    invalidatePreviewImage()
                    invalidateLoad()
                    _state.update {
                        it.copy(
                            loading = false,
                            rollingBack = false,
                            active = result.value.active,
                            previous = result.value.previous,
                            fallback = result.value.fallback,
                            preview = null,
                            previewBytes = null,
                            error = null,
                        )
                    }
                    emit("已回滚到上一版素材")
                }

                is AppResult.Err -> _state.update {
                    it.copy(rollingBack = false, error = result.error.message)
                }
            }
        }
    }

    /** 拉取 active（没有则占位图）的第 0 帧；超过 1 MiB 时放弃展示。 */
    fun loadPreviewImage() {
        if (!isOnline()) return
        val target = _state.value.active?.id ?: "fallback"
        // 连点刷新 / 确认 / 回滚会让 active 快速变化；旧请求的迟到响应不能
        // 覆盖新包的首帧，也不能把旧包的 404 报成当前页的错误。
        previewGeneration += 1
        val generation = previewGeneration
        previewJob?.cancel()
        previewJob = viewModelScope.launch {
            val result = repository.frameBytes(target, 0)
            if (generation != previewGeneration) return@launch
            when (result) {
                is AppResult.Ok -> {
                    if (result.value.size > MAX_PREVIEW_BYTES) {
                        _state.update {
                            it.copy(previewBytes = null, error = "首帧超过 1 MiB，已跳过预览")
                        }
                    } else {
                        // 不在这里清 error：避免图片请求成功时抹掉刚发生的
                        // 上传/确认错误。
                        _state.update { it.copy(previewBytes = result.value) }
                    }
                }

                // 同一个包的一次瞬时失败不清空已显示的首帧；跨包串图由
                // generation + activeChanged 处理。
                is AppResult.Err -> _state.update {
                    it.copy(error = result.error.message)
                }
            }
        }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    private fun invalidatePreviewImage() {
        previewGeneration += 1
        previewJob?.cancel()
        previewJob = null
    }

    /** 让在途的 `load()` 响应过期（例如期间确认/回滚成功）。 */
    private fun invalidateLoad() {
        loadGeneration += 1
    }

    private fun requireIdle(): Boolean {
        if (!isOnline()) {
            _state.update { it.copy(error = "当前离线，无法执行该操作") }
            return false
        }
        if (_state.value.busy) return false
        return true
    }

    private fun emit(text: String) {
        _events.tryEmit(AssetsEvent.Message(text))
    }
}

private fun AppError.isExpiredUpload(): Boolean =
    this is AppError.Http && code == "upload_expired"
