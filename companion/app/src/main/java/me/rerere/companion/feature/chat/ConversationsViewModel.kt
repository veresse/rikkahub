package me.rerere.companion.feature.chat

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
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.repo.ConversationRepository

data class ConversationsUiState(
    val loading: Boolean = true,
    val items: List<ConversationDto> = emptyList(),
    val error: String? = null,
    val creating: Boolean = false,
    val deletingId: String? = null,
    val snackbar: String? = null,
)

sealed interface ConversationsEvent {
    data class Created(val conversationId: String) : ConversationsEvent
    data class Message(val text: String) : ConversationsEvent
}

/**
 * 会话列表。
 *
 * 「生成中」角标不在这里：它来自 `AppWsManager.turnStates`，
 * 由页面按 `conversation.id` 查表，避免 WS 事件和 REST 刷新互相覆盖。
 */
class ConversationsViewModel(
    private val repository: ConversationRepository,
    private val assistantId: String,
    autoLoad: Boolean = true,
) : ViewModel() {

    private val _state = MutableStateFlow(ConversationsUiState())
    val state: StateFlow<ConversationsUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<ConversationsEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<ConversationsEvent> = _events.asSharedFlow()

    /** 递增后，旧的列表请求结果不再覆盖新的列表状态。 */
    private var refreshGeneration = 0

    init {
        // 页面每次进入都会主动刷新（聊天返回后标题/排序需要更新）；
        // 测试或其他调用方仍可用默认值在构造时加载。
        if (autoLoad) refresh()
    }

    fun refresh() {
        // 自动刷新与删除/新建可能并发：旧请求晚回来不得把已删除的会话“复活”。
        val generation = ++refreshGeneration
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val result = repository.list(assistantId)) {
                is AppResult.Ok -> {
                    if (generation != refreshGeneration) return@launch
                    _state.update {
                        it.copy(loading = false, items = result.value, error = null)
                    }
                }

                is AppResult.Err -> {
                    if (generation != refreshGeneration) return@launch
                    _state.update { current ->
                        current.copy(
                            loading = false,
                            error = result.error.message,
                            // 已有列表时错误区不显示；用 snackbar 明确告知刷新失败。
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
    }

    fun create() {
        if (_state.value.creating) return
        _state.update { it.copy(creating = true) }
        viewModelScope.launch {
            when (val result = repository.create(assistantId)) {
                is AppResult.Ok -> {
                    _state.update { it.copy(creating = false) }
                    _events.tryEmit(ConversationsEvent.Created(result.value))
                    refresh()
                }

                is AppResult.Err -> {
                    _state.update { it.copy(creating = false) }
                    _events.tryEmit(ConversationsEvent.Message(result.error.message))
                }
            }
        }
    }

    fun delete(id: String) {
        if (_state.value.deletingId != null) {
            _events.tryEmit(ConversationsEvent.Message("上一个删除还在进行中"))
            return
        }
        // 只作废“删除开始前”发出的列表请求：删除开始后用户手动刷新拿到的
        // 是删除后的列表，不能被误杀，否则列表停在旧数据上。
        val generationAtDeleteStart = refreshGeneration
        _state.update { it.copy(deletingId = id) }
        viewModelScope.launch {
            when (val result = repository.delete(id)) {
                is AppResult.Ok -> {
                    // 作废在途的列表请求：它拿到的是删除前的快照，返回后会把会话“复活”。
                    if (refreshGeneration == generationAtDeleteStart) {
                        refreshGeneration += 1
                    }
                    _state.update { current ->
                        current.copy(
                            deletingId = null,
                            loading = false,
                            items = current.items.filterNot { it.id == id },
                        )
                    }
                    _events.tryEmit(ConversationsEvent.Message("已删除对话"))
                }

                is AppResult.Err -> {
                    _state.update { it.copy(deletingId = null) }
                    _events.tryEmit(ConversationsEvent.Message(result.error.message))
                }
            }
        }
    }

    fun dismissSnackbar() {
        _state.update { it.copy(snackbar = null) }
    }
}
