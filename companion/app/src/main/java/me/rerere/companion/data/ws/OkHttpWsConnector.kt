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
                    trySend(WsFrame.Open)
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
