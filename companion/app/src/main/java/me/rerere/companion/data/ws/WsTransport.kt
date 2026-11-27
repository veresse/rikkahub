package me.rerere.companion.data.ws

import kotlinx.coroutines.flow.Flow

/** WebSocket 传输层的最小帧模型，便于用假实现测试重连状态机。 */
sealed interface WsFrame {
    /** 握手成功（HTTP 101）。 */
    data object Open : WsFrame
    data class Text(val json: String) : WsFrame
    data class Closed(val code: Int, val reason: String) : WsFrame
    data class Failed(val cause: Throwable) : WsFrame
}

fun interface WsConnector {
    fun connect(url: String, token: String): Flow<WsFrame>
}
