package me.rerere.companion.feature.assistants

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.repo.AssistantRepository
import kotlinx.serialization.json.JsonObject

data class AssistantEditUiState(
    val loading: Boolean = false,
    val saving: Boolean = false,
    val loadFailed: Boolean = false,
    val error: String? = null,
    val name: String = "",
    val systemPrompt: String = "",
    val modelId: String = "",
    val contextMessageLimit: String = "0",
    val temperature: String = "0.7",
    val topP: String = "0.9",
    val enableMemory: Boolean = false,
    val useGlobalMemory: Boolean = false,
    val enableActionSync: Boolean = true,
    val webSearchMode: String = "off",
    /** 原样回传，避免保存时把服务端已有的头像覆盖成空对象。 */
    val avatar: JsonObject = JsonObject(emptyMap()),
)

sealed interface AssistantEditEvent {
    data object Saved : AssistantEditEvent
    data class Message(val text: String) : AssistantEditEvent
}

class AssistantEditViewModel(
    private val repository: AssistantRepository,
    private val assistantId: String?,
) : ViewModel() {

    private val _state = MutableStateFlow(AssistantEditUiState(loading = assistantId != null))
    val state: StateFlow<AssistantEditUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<AssistantEditEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<AssistantEditEvent> = _events.asSharedFlow()

    init {
        if (assistantId != null) load(assistantId)
    }

    private fun load(id: String) {
        _state.update { it.copy(loading = true, loadFailed = false, error = null) }
        viewModelScope.launch {
            when (val result = repository.get(id)) {
                is AppResult.Ok -> _state.update { it.fromDto(result.value) }
                is AppResult.Err -> _state.update {
                    it.copy(loading = false, loadFailed = true, error = result.error.message)
                }
            }
        }
    }

    /** 加载失败后只允许重试/返回，不能拿空表单保存覆盖原角色。 */
    fun retryLoad() {
        val id = assistantId ?: return
        load(id)
    }

    fun update(transform: (AssistantEditUiState) -> AssistantEditUiState) {
        _state.update(transform)
    }

    fun save() {
        val snapshot = _state.value
        if (snapshot.saving || snapshot.loadFailed) return
        val draft = snapshot.toDraft() ?: return
        _state.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            val result = if (assistantId == null) {
                repository.create(draft)
            } else {
                repository.update(assistantId, draft)
            }
            when (result) {
                is AppResult.Ok -> {
                    _state.update { it.copy(saving = false) }
                    _events.tryEmit(AssistantEditEvent.Saved)
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false, error = result.error.message) }
                    _events.tryEmit(AssistantEditEvent.Message(result.error.message))
                }
            }
        }
    }

    /** 返回 null 时已把可读错误写进 state，且不会调用仓库。 */
    private fun AssistantEditUiState.toDraft(): AssistantDraft? {
        val trimmedName = name.trim()
        if (trimmedName.isEmpty()) {
            _state.update { it.copy(error = "请填写角色名称") }
            return null
        }
        val temperatureValue = temperature.trim().toDoubleOrNull()
        if (temperatureValue == null || temperatureValue !in 0.0..2.0) {
            _state.update { it.copy(error = "温度必须在 0 到 2 之间") }
            return null
        }
        val topPValue = topP.trim().toDoubleOrNull()
        if (topPValue == null || topPValue !in 0.0..1.0) {
            _state.update { it.copy(error = "top_p 必须在 0 到 1 之间") }
            return null
        }
        val limit = contextMessageLimit.trim().toIntOrNull()
        if (limit == null || limit < 0) {
            _state.update { it.copy(error = "上下文条数必须是不小于 0 的整数") }
            return null
        }
        return AssistantDraft(
            name = trimmedName,
            systemPrompt = systemPrompt,
            temperature = temperatureValue,
            topP = topPValue,
            contextMessageLimit = limit,
            enableMemory = enableMemory,
            useGlobalMemory = useGlobalMemory,
            webSearchMode = webSearchMode,
            enableActionSync = enableActionSync,
            modelId = modelId.trim().ifBlank { null },
            avatar = avatar,
        )
    }

    private fun AssistantEditUiState.fromDto(dto: AssistantDto): AssistantEditUiState = copy(
        loading = false,
        error = null,
        name = dto.name,
        systemPrompt = dto.systemPrompt,
        modelId = dto.modelId.orEmpty(),
        contextMessageLimit = dto.contextMessageLimit.toString(),
        temperature = (dto.temperature ?: 0.7).toString(),
        topP = (dto.topP ?: 0.9).toString(),
        enableMemory = dto.enableMemory,
        useGlobalMemory = dto.useGlobalMemory,
        enableActionSync = dto.enableActionSync,
        webSearchMode = dto.webSearchMode,
        avatar = dto.avatar,
    )
}
