package me.rerere.companion.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.ChatRequestDto
import me.rerere.companion.data.api.dto.ConversationDetailDto
import me.rerere.companion.data.repo.ChatRepository
import me.rerere.companion.data.repo.ConversationRepository
import me.rerere.companion.data.sse.ChatEvent
import me.rerere.companion.data.ws.TurnState
import me.rerere.companion.data.ws.WsEvent

data class ChatMessageUi(
    val key: String,
    val role: String,
    val text: String = "",
    val reasoning: String = "",
    val nodeId: String? = null,
    val messageId: String? = null,
    val userMessageId: String? = null,
    val streaming: Boolean = false,
    val interrupted: Boolean = false,
    val failed: Boolean = false,
)

data class ChatUiState(
    val loading: Boolean = true,
    val error: String? = null,
    val title: String = "",
    val assistantName: String = "",
    val messages: List<ChatMessageUi> = emptyList(),
    val input: String = "",
    val sending: Boolean = false,
    val generating: Boolean = false,
    val voiceDraft: String? = null,
    val voiceInterrupted: Boolean = false,
    val correctionTarget: ChatMessageUi? = null,
    val correcting: Boolean = false,
    val snackbar: String? = null,
)

/**
 * 聊天页状态机。
 *
 * - 一次只允许一个 [streamJob]；发送中/生成中禁止再次发送。
 * - `generating` = 本地流式 || 服务端 `conversation.turn_state(generating)`，
 *   因此别的端（语音/网页）在生成时也能正确禁用发送。
 * - 失败消息保留原始 `client_msg_id`，[retry] 用它重试，服务端据此幂等。
 * - 纠错只改已落库的 USER 文本，不触发重新生成（UI 明确提示“回答未更新”）。
 */
class ChatViewModel(
    private val conversationId: String,
    private val assistantName: String,
    private val conversationRepository: ConversationRepository,
    private val chatRepository: ChatRepository,
    turnStates: StateFlow<Map<String, TurnState>>? = null,
    wsEvents: Flow<WsEvent>? = null,
    /** 写操作门控：断网时即使 UI 状态过期，也不能发起请求。 */
    private val isOnline: () -> Boolean = { true },
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatUiState(
            title = assistantName,
            assistantName = assistantName,
        ),
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var streamJob: Job? = null
    private var pendingTurn: PendingTurn? = null
    private val failedTurns = mutableMapOf<String, PendingTurn>()

    /** 递增后，旧的 `conversation.detail` 结果不再覆盖屏幕。 */
    private var reloadGeneration = 0

    /** 当前语音轮次：取消清理在后台跑，旧轮的 turn_end 可能晚于新一轮 turn_start。 */
    private var activeVoiceTurnId: String? = null

    private var remoteGenerating = false
    private var localStreaming = false

    init {
        if (turnStates != null) {
            viewModelScope.launch {
                turnStates.collect { map ->
                    remoteGenerating = map[conversationId]?.state == "generating"
                    refreshGenerating()
                }
            }
        }
        if (wsEvents != null) {
            viewModelScope.launch {
                wsEvents.collect { event ->
                    if (event is WsEvent.Voice) onVoiceEvent(event)
                }
            }
        }
        reload()
    }

    fun reload() {
        // 流式生成中重建列表会把本地临时消息替换成服务端节点，后续 delta 写不进去。
        if (localStreaming || _state.value.sending) return
        val generation = ++reloadGeneration
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            when (val result = conversationRepository.detail(conversationId)) {
                is AppResult.Ok -> {
                    if (generation != reloadGeneration) return@launch
                    _state.update {
                        it.copy(
                            loading = false,
                            error = null,
                            title = result.value.title.ifBlank { assistantName },
                            messages = result.value.toUiMessages(),
                        )
                    }
                }

                is AppResult.Err -> {
                    if (generation != reloadGeneration) return@launch
                    _state.update { current ->
                        current.copy(
                            loading = false,
                            error = result.error.message,
                            snackbar = if (current.messages.isNotEmpty()) {
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

    fun onInputChange(value: String) {
        _state.update { it.copy(input = value) }
    }

    fun useVoiceDraft() {
        _state.update { current ->
            val draft = current.voiceDraft ?: return@update current
            current.copy(input = draft, voiceDraft = null)
        }
    }

    fun dismissVoiceDraft() {
        _state.update { it.copy(voiceDraft = null) }
    }

    fun send() {
        if (!isOnline()) {
            _state.update { it.copy(snackbar = "当前离线，无法发送") }
            return
        }
        val snapshot = _state.value
        if (snapshot.sending || snapshot.generating) return
        // 历史还在加载时发送会作废这次加载，新回答之外的历史就永久丢了。
        if (snapshot.loading) return
        val text = snapshot.input.trim()
        if (text.isEmpty()) return

        val clientMsgId = UUID.randomUUID().toString()
        val turn = PendingTurn(
            request = ChatRequestDto(
                conversationId = conversationId,
                message = text,
                clientMsgId = clientMsgId,
            ),
            userKey = "local-user-$clientMsgId",
            assistantKey = "local-assistant-$clientMsgId",
            userText = text,
        )
        _state.update { current ->
            current.copy(
                input = "",
                voiceDraft = null,
                snackbar = null,
                messages = current.messages +
                    ChatMessageUi(key = turn.userKey, role = "USER", text = text) +
                    ChatMessageUi(key = turn.assistantKey, role = "ASSISTANT", streaming = true),
            )
        }
        runTurn(turn)
    }

    /** 只重试被点击的那条失败消息，复用它的 `client_msg_id`。 */
    fun retry(messageKey: String) {
        if (!isOnline()) {
            _state.update { it.copy(snackbar = "当前离线，无法重试") }
            return
        }
        if (_state.value.sending || _state.value.generating) return
        val turn = failedTurns[messageKey] ?: return
        _state.update { current ->
            current.copy(
                messages = current.messages.map { message ->
                    if (message.key == turn.assistantKey) {
                        message.copy(
                            text = "",
                            reasoning = "",
                            streaming = true,
                            failed = false,
                            interrupted = false,
                        )
                    } else {
                        message
                    }
                },
                snackbar = null,
            )
        }
        runTurn(turn)
    }

    fun stop() {
        val turn = pendingTurn ?: return
        streamJob?.cancel()
        streamJob = null
        pendingTurn = null
        localStreaming = false
        _state.update { current ->
            val messages = current.messages.mapNotNull { message ->
                when {
                    message.key != turn.assistantKey -> message
                    message.text.isBlank() && message.reasoning.isBlank() -> null
                    else -> message.copy(streaming = false, interrupted = true)
                }
            }
            current.copy(messages = messages, sending = false, snackbar = "已停止生成")
        }
        refreshGenerating()
    }

    fun startCorrection(key: String) {
        val target = _state.value.messages.firstOrNull {
            it.key == key && it.role == "USER"
        } ?: return
        _state.update { it.copy(correctionTarget = target) }
    }

    fun cancelCorrection() {
        _state.update { it.copy(correctionTarget = null) }
    }

    fun correct(text: String) {
        if (!isOnline()) {
            _state.update { it.copy(snackbar = "当前离线，无法修改") }
            return
        }
        if (_state.value.correcting) return
        val target = _state.value.correctionTarget ?: return
        val nodeId = target.nodeId
        val messageId = target.userMessageId
        val trimmed = text.trim()
        if (nodeId == null || messageId == null) {
            _state.update {
                it.copy(correctionTarget = null, snackbar = "该消息尚未保存，暂不能纠正")
            }
            return
        }
        if (trimmed.isEmpty()) return
        _state.update { it.copy(correcting = true) }
        viewModelScope.launch {
            when (
                val result = conversationRepository.correctMessage(
                    conversationId = conversationId,
                    nodeId = nodeId,
                    messageId = messageId,
                    text = trimmed,
                )
            ) {
                is AppResult.Ok -> _state.update { current ->
                    current.copy(
                        correcting = false,
                        correctionTarget = null,
                        messages = current.messages.map {
                            if (it.key == target.key) it.copy(text = trimmed) else it
                        },
                        snackbar = "已更新文本，回答未更新，可重新发送",
                    )
                }

                is AppResult.Err -> _state.update { current ->
                    current.copy(correcting = false, snackbar = result.error.message)
                }
            }
        }
    }

    fun dismissSnackbar() {
        _state.update { it.copy(snackbar = null) }
    }

    override fun onCleared() {
        streamJob?.cancel()
        super.onCleared()
    }

    private fun runTurn(turn: PendingTurn) {
        streamJob?.cancel()
        // 任何新轮次都让进行中的 reload 结果失效，避免它覆盖刚追加的本地消息。
        reloadGeneration += 1
        pendingTurn = turn
        localStreaming = true
        // 同时清掉被作废的 reload 的 loading，避免刷新按钮永久禁用。
        _state.update { it.copy(sending = true, loading = false) }
        refreshGenerating()

        streamJob = viewModelScope.launch {
            var terminal = false
            try {
                chatRepository.stream(turn.request)
                    .onCompletion { cause ->
                        if (cause == null && !terminal) {
                            markFailed(turn, "连接提前结束，请重试")
                        }
                    }
                    .catch { throwable ->
                        if (throwable is CancellationException) throw throwable
                        // done 之后的代理 RST/close 异常不能把完整回答改成失败。
                        if (!terminal) {
                            // 不把库/解析异常的内部文案透给用户。
                            markFailed(turn, "生成失败，请重试")
                        }
                    }
                    .collect { event ->
                        if (event is ChatEvent.Done || event is ChatEvent.Failed) {
                            terminal = true
                        }
                        handleEvent(turn, event)
                    }
            } finally {
                // 旧 job 的 finally 不能清掉新轮次的 sending/localStreaming。
                if (pendingTurn === turn) {
                    pendingTurn = null
                    localStreaming = false
                    _state.update { it.copy(sending = false) }
                    refreshGenerating()
                }
            }
        }
    }

    private fun handleEvent(turn: PendingTurn, event: ChatEvent) {
        // stop/重试后旧流的迟到事件不得写进新轮次。
        if (pendingTurn !== turn) return
        when (event) {
            is ChatEvent.Content -> updateMessage(turn.assistantKey) {
                it.copy(text = it.text + event.delta)
            }

            is ChatEvent.Reasoning -> updateMessage(turn.assistantKey) {
                it.copy(reasoning = it.reasoning + event.delta)
            }

            is ChatEvent.Status -> {
                if (event.status == "searching") {
                    // 服务端文案可能含内部细节，这里只展示固定提示。
                    _state.update { it.copy(snackbar = "正在联网搜索…") }
                }
            }

            is ChatEvent.ToolResult, is ChatEvent.Usage -> Unit

            is ChatEvent.Failed -> markFailed(turn, event.message, event.retryAfterSeconds)

            is ChatEvent.Done -> {
                failedTurns.remove(turn.assistantKey)
                val assistantId = event.assistantMessageId
                val userMessageId = event.userMessageId
                _state.update { current ->
                    current.copy(
                        messages = current.messages.map { message ->
                            when (message.key) {
                                turn.userKey -> message.copy(
                                    userMessageId = userMessageId ?: message.userMessageId,
                                    // 普通轮次的用户节点 id 就是消息 id，纠错可直接用。
                                    nodeId = message.nodeId ?: userMessageId,
                                )

                                turn.assistantKey -> message.copy(
                                    messageId = assistantId ?: message.messageId,
                                    streaming = false,
                                    interrupted = false,
                                    failed = false,
                                )

                                else -> message
                            }
                        },
                        sending = false,
                    )
                }
                localStreaming = false
                refreshGenerating()
                if (event.replayed) reload()
            }
        }
    }

    private fun markFailed(turn: PendingTurn, message: String, retryAfterSeconds: Int? = null) {
        // 已经被新轮次取代的旧流即使抛出非取消异常，也不能动新轮次的状态。
        if (pendingTurn !== turn) return
        failedTurns[turn.assistantKey] = turn
        val text = if (retryAfterSeconds != null && retryAfterSeconds > 0) {
            "$message（约 $retryAfterSeconds 秒后可重试）"
        } else {
            message
        }
        _state.update { current ->
            current.copy(
                messages = current.messages.map {
                    if (it.key == turn.assistantKey) {
                        it.copy(streaming = false, failed = true)
                    } else {
                        it
                    }
                },
                snackbar = text,
                sending = false,
            )
        }
    }

    private fun updateMessage(key: String, transform: (ChatMessageUi) -> ChatMessageUi) {
        _state.update { current ->
            current.copy(
                messages = current.messages.map {
                    if (it.key == key) transform(it) else it
                },
            )
        }
    }

    private fun onVoiceEvent(event: WsEvent.Voice) {
        // 没有 conversation_id 的事件无法判断归属；宁可丢弃，也不能把 A 会话的
        // 转写/中断状态串到 B 会话（历史泄漏）。
        if (event.conversationId != conversationId) return
        when (event.event) {
            "turn_start" -> activeVoiceTurnId = event.turnId

            "asr_final" -> {
                val text = event.text?.trim().orEmpty()
                if (text.isNotEmpty()) {
                    _state.update { it.copy(voiceDraft = text) }
                }
            }

            "turn_end" -> {
                // 旧轮的取消清理可能在新轮 turn_start 之后才发出 turn_end；
                // 用 turn_id 守卫，避免把上一轮的中断状态贴到新轮。
                // 有活跃轮次时，缺少 turn_id 的 end 也无法证明属于本轮，一律丢弃。
                val active = activeVoiceTurnId
                if (active != null && event.turnId != active) return
                activeVoiceTurnId = null
                _state.update { it.copy(voiceInterrupted = event.interrupted) }
            }
        }
    }

    private fun refreshGenerating() {
        _state.update { it.copy(generating = localStreaming || remoteGenerating) }
    }

    private data class PendingTurn(
        val request: ChatRequestDto,
        val userKey: String,
        val assistantKey: String,
        val userText: String,
    )
}

private fun ConversationDetailDto.toUiMessages(): List<ChatMessageUi> =
    nodes.sortedBy { it.nodeIndex }.mapNotNull { node ->
        val message = node.messages.getOrNull(node.selectIndex) ?: return@mapNotNull null
        val role = message.role.uppercase()
        val id = message.id.ifBlank { null }
        ChatMessageUi(
            key = "node-${node.id}",
            role = role,
            text = message.parts.mapNotNull { it.text }.joinToString("\n"),
            reasoning = message.parts.mapNotNull { it.reasoning }.joinToString("\n"),
            nodeId = node.id,
            messageId = id,
            userMessageId = if (role == "USER") id else null,
        )
    }
