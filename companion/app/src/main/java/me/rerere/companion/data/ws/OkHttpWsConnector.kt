package me.rerere.companion.data.ws

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/**
 * 真实 WebSocket 连接：独立 client，读超时关闭、25s 心跳。
 *
 * Token 只放 `Authorization` 头，不放 URL（避免被日志/代理记录）。
 */
class OkHttpWsConnector(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // 0 表示不设读超时，长连接靠 ping/pong 保活。
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(25, TimeUnit.SECONDS)
        .build(),
) : WsConnector {

    override fun connect(url: String, token: String): Flow<WsFrame> = callbackFlow {
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .build()
        val socket = client.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    // 先发可写句柄再发 Open：管理器一进入 Connected 就能 send()，
                    // 否则紧跟在 Connected 之后的命令会因 sink 还没发布而“未送达”。
                    val writable = trySend(
                        WsFrame.Writable(WsSink { webSocket.send(it) }),
                    ).isSuccess
                    if (!writable) {
                        // 下游已关闭/缓冲满：不要再宣布 Connected。
                        webSocket.cancel()
                        return
                    }
                    // Open 发不出去同样致命：sink 已发布但没有 Connected，缓存不会清、
                    // 页面也不会重拉快照，等于半死连接。
                    if (!trySend(WsFrame.Open).isSuccess) {
                        webSocket.cancel()
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    trySend(WsFrame.Text(text))
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    trySend(WsFrame.Closed(code, reason))
                    close()
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    trySend(WsFrame.Failed(t))
                    close()
                }
            },
        )
        awaitClose { socket.cancel() }
    }
}
