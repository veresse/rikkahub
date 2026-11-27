package me.rerere.companion.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.MemoryDto
import me.rerere.companion.data.repo.MemoryRepository

data class MemoryEditor(
    val id: String?,
    val content: String,
)

data class MemoriesUiState(
    val loading: Boolean = true,
    val items: List<MemoryDto> = emptyList(),
    val error: String? = null,
    val editor: MemoryEditor? = null,
    val saving: Boolean = false,
    val deletingId: String? = null,
    val snackbar: String? = null,
)

/**
 * 角色记忆列表。
 *
 * [assistantId] 为 null 表示全局记忆；服务端对两者用同一套接口。
 */
class MemoriesViewModel(
    private val repository: MemoryRepository,
    private val assistantId: String?,
) : ViewModel() {

    private val _state = MutableStateFlow(MemoriesUiState())
    val state: StateFlow<MemoriesUiState> = _state.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val result = repository.list(assistantId)) {
                is AppResult.Ok -> _state.update {
                    it.copy(loading = false, items = result.value, error = null)
                }

                is AppResult.Err -> _state.update { current ->
                    current.copy(
                        loading = false,
                        error = result.error.message,
                        snackbar = if (current.items.isNotEmpty()) {
                            result.error.message
                        } else {
                            current.snackbar
                        },
                    )
                }
            }
        }
    }

    fun startCreate() {
        _state.update { it.copy(editor = MemoryEditor(id = null, content = "")) }
    }

    fun startEdit(memory: MemoryDto) {
        _state.update { it.copy(editor = MemoryEditor(id = memory.id, content = memory.content)) }
    }

    fun updateContent(text: String) {
        _state.update { current ->
            val editor = current.editor ?: return@update current
            current.copy(editor = editor.copy(content = text))
        }
    }

    fun closeEditor() {
        _state.update { it.copy(editor = null) }
    }

    fun save() {
        val snapshot = _state.value
        val editor = snapshot.editor ?: return
        val content = editor.content.trim()
        if (snapshot.saving || content.isEmpty()) return
        _state.update { it.copy(saving = true) }
        viewModelScope.launch {
            val result = if (editor.id == null) {
                repository.create(assistantId, content)
            } else {
                repository.update(editor.id, content)
            }
            when (result) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(saving = false, editor = null, snackbar = "已保存记忆")
                    }
                    refresh()
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false, snackbar = result.error.message) }
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
                            snackbar = "已删除记忆",
                        )
                    }
                }

                is AppResult.Err -> {
                    _state.update {
                        it.copy(deletingId = null, snackbar = result.error.message)
                    }
                }
            }
        }
    }

    fun dismissSnackbar() {
        _state.update { it.copy(snackbar = null) }
    }
}
