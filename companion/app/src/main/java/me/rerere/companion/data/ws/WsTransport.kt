package me.rerere.companion.data.ws

import kotlinx.coroutines.flow.Flow

/**
 * 已建立连接的可写句柄：连接断开/收尾后调用 [send] 必须返回 `false`，
 * 调用方不得把 `false` 当成“命令已送达”。
 */
fun interface WsSink {
    fun send(text: String): Boolean
}

/** WebSocket 传输层的最小帧模型，便于用假实现测试重连状态机。 */
sealed interface WsFrame {
    /** 握手成功（HTTP 101）。 */
    data object Open : WsFrame

    /** 通道可写；与 [Open] 配对出现，连接断开后失效。 */
    data class Writable(val sink: WsSink) : WsFrame

    data class Text(val json: String) : WsFrame
    data class Closed(val code: Int, val reason: String) : WsFrame
    data class Failed(val cause: Throwable) : WsFrame
}

fun interface WsConnector {
    fun connect(url: String, token: String): Flow<WsFrame>
}
