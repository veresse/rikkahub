package me.rerere.companion.data.ws

import kotlinx.serialization.json.JsonObject

/** `/app/ws` 下发的已知事件（`v == 2`），未知类型直接忽略。 */
sealed interface WsEvent {
    data class DeviceStatus(
        val deviceId: String,
        val online: Boolean,
        val reason: String,
    ) : WsEvent

    data class DeviceReport(
        val deviceId: String,
        val report: JsonObject,
    ) : WsEvent

    data class ActionResult(
        val deviceId: String,
        val actionId: String,
        val status: String,
        val detail: String,
        val source: String,
    ) : WsEvent

    data class RemoteAck(
        val requestId: String,
        val ok: Boolean,
        val error: String?,
        val actionId: String?,
    ) : WsEvent

    data class Voice(
        val event: String,
        val deviceId: String?,
        val turnId: String?,
        val text: String?,
        val conversationId: String?,
        val userMessageId: String?,
        val finishReason: String?,
        val interrupted: Boolean,
    ) : WsEvent

    data class TurnStateChanged(val state: TurnState) : WsEvent

    data class ServerError(val code: String, val message: String) : WsEvent
}

data class TurnState(
    val conversationId: String,
    val state: String,
    val source: String,
    val turnKey: String,
)

data class DeviceStatus(
    val deviceId: String,
    val online: Boolean,
    val reason: String = "",
)

sealed interface ConnectionState {
    data object Stopped : ConnectionState
    data object Connecting : ConnectionState
    data object Connected : ConnectionState
    data class Retrying(val attempt: Int, val delayMillis: Long) : ConnectionState
}
