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
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.repo.AssistantRepository

data class AssistantsUiState(
    val loading: Boolean = true,
    val items: List<AssistantDto> = emptyList(),
    val error: String? = null,
    val deletingId: String? = null,
)

sealed interface AssistantsEvent {
    data class Message(val text: String) : AssistantsEvent
}

class AssistantsViewModel(
    private val repository: AssistantRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(AssistantsUiState())
    val state: StateFlow<AssistantsUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<AssistantsEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<AssistantsEvent> = _events.asSharedFlow()

    init {
        refresh()
    }

    fun refresh() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val result = repository.list()) {
                is AppResult.Ok -> _state.update {
                    it.copy(loading = false, items = result.value, error = null)
                }

                is AppResult.Err -> {
                    _state.update {
                        it.copy(loading = false, error = result.error.message)
                    }
                    // 已有列表时错误区域不显示，必须用 snackbar 明确告知刷新失败。
                    if (_state.value.items.isNotEmpty()) {
                        _events.tryEmit(AssistantsEvent.Message(result.error.message))
                    }
                }
            }
        }
    }

    fun delete(id: String) {
        if (_state.value.deletingId != null) return
        _state.update { it.copy(deletingId = id) }
        viewModelScope.launch {
            when (val result = repository.delete(id)) {
                is AppResult.Ok -> {
                    _state.update { current ->
                        current.copy(
                            deletingId = null,
                            items = current.items.filterNot { it.id == id },
                        )
                    }
                    _events.tryEmit(AssistantsEvent.Message("已删除角色"))
                }

                is AppResult.Err -> {
                    _state.update { it.copy(deletingId = null) }
                    _events.tryEmit(AssistantsEvent.Message(result.error.message))
                }
            }
        }
    }
}
