package me.rerere.companion.data.sse

/**
 * 服务端 SSE 事件（`POST /api/chat/completions` 的 `data:` 帧）。
 *
 * 未知字段/未知类型一律忽略，保证服务端加字段不会让 App 崩溃。
 */
sealed interface ChatEvent {
    data class Content(val delta: String) : ChatEvent
    data class Reasoning(val delta: String) : ChatEvent
    data class Status(val status: String, val message: String? = null) : ChatEvent
    data class ToolResult(val tool: String?, val resultJson: String) : ChatEvent
    data class Usage(val json: String) : ChatEvent
    data class Failed(
        val code: String,
        val message: String,
        val retryAfterSeconds: Int? = null,
    ) : ChatEvent
    data class Done(
        val finishReason: String,
        val assistantMessageId: String?,
        val userMessageId: String?,
        val replayed: Boolean,
    ) : ChatEvent
}
