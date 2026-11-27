package me.rerere.companion.data.ws

import java.net.URI
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import me.rerere.companion.data.store.StoredSession

/**
 * App 端 `/app/ws` 连接管理器。
 *
 * - 指数退避（1/2/4/8/16/30 秒，可注入 jitter），网络恢复立即重试；
 * - 4401 只清退 Token，不重连；普通断线/1011/1012 必须重连；
 * - `conversation.turn_state` 用 `turn_key` 二次守卫，旧轮迟到 idle 不会清新轮。
 */
class AppWsManager(
    private val connector: WsConnector,
    private val sessionProvider: () -> StoredSession?,
    private val onUnauthorized: (token: String) -> Unit,
    private val scope: CoroutineScope,
    private val jitter: (Long) -> Long = { it },
    private val now: () -> Long = System::currentTimeMillis,
) {
    private val json = Json { ignoreUnknownKeys = true }

    private val _connection = MutableStateFlow<ConnectionState>(ConnectionState.Stopped)
    val connection: StateFlow<ConnectionState> = _connection.asStateFlow()

    private val _devices = MutableStateFlow<Map<String, DeviceStatus>>(emptyMap())
    val devices: StateFlow<Map<String, DeviceStatus>> = _devices.asStateFlow()

    private val _turnStates = MutableStateFlow<Map<String, TurnState>>(emptyMap())
    val turnStates: StateFlow<Map<String, TurnState>> = _turnStates.asStateFlow()

    private val _events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 32)
    val events: SharedFlow<WsEvent> = _events.asSharedFlow()

    private val networkEpoch = MutableStateFlow(0L)

    @Volatile
    private var running = false
    private var job: Job? = null

    fun start() {
        if (running) return
        running = true
        job = scope.launch { runLoop() }
    }

    fun stop() {
        running = false
        job?.cancel()
        job = null
        _connection.value = ConnectionState.Stopped
        // 登出/停止后不得把上一个账号的设备与生成状态留给下一个账号。
        _devices.value = emptyMap()
        _turnStates.value = emptyMap()
    }

    fun onNetworkAvailable() {
        // 用序号而不是一次性事件：连接尝试期间发出的恢复信号也不会丢。
        networkEpoch.update { it + 1 }
    }

    private suspend fun runLoop() {
        var attempt = 0
        var unauthorized = false
        try {
            while (currentCoroutineContext().isActive && running) {
                val session = sessionProvider() ?: break
                val epochBefore = networkEpoch.value
                var openedAt: Long? = null
                _connection.value = ConnectionState.Connecting
                try {
                    connector.connect(wsUrl(session.serverUrl), session.token).collect { frame ->
                        if (!running) return@collect
                        when (frame) {
                            is WsFrame.Open -> {
                                openedAt = now()
                                // 新连接不继承上一连接的生成状态：断线期间的 idle 帧已丢，
                                // 保留会让该会话永久“生成中”而无法再发送。
                                _turnStates.value = emptyMap()
                                _connection.value = ConnectionState.Connected
                            }

                            is WsFrame.Text -> {
                                _connection.value = ConnectionState.Connected
                                handle(frame.json)
                            }

                            is WsFrame.Closed -> {
                                if (frame.code == CLOSE_TOKEN_REVOKED) {
                                    unauthorized = true
                                    running = false
                                    onUnauthorized(session.token)
                                }
                            }

                            is WsFrame.Failed -> Unit
                        }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) {
                    // 连接层异常按普通断线处理，日志不得包含事件载荷。
                }
                if (!running || unauthorized) break

                // 断线期间的 idle 帧会丢；保留旧 generating 会让会话永久无法发送。
                _turnStates.value = emptyMap()

                // 只有连接稳定存活过一段时间才算真正成功；否则“接受后立刻关闭”
                // 会让客户端永远从 1s 起步，形成重连风暴。
                val opened = openedAt
                if (opened != null && now() - opened >= STABLE_CONNECTION_MILLIS) {
                    attempt = 0
                }

                val epoch = networkEpoch.value
                val recovered = epoch != epochBefore
                val delayMillis = if (recovered) 0L else jitter(backoffDelay(attempt))
                attempt += 1
                _connection.value = ConnectionState.Retrying(attempt, delayMillis)
                if (delayMillis > 0) {
                    withTimeoutOrNull(delayMillis) {
                        networkEpoch.first { it != epoch }
                    }
                }
            }
        } finally {
            _connection.value = ConnectionState.Stopped
        }
    }

    /** 只处理 `v == 2` 的已知类型；未知/坏帧静默丢弃。 */
    private fun handle(raw: String) {
        if (!running) return
        val payload = runCatching {
            json.parseToJsonElement(raw) as? JsonObject
        }.getOrNull() ?: return
        if (payload.int("v") != 2) return

        when (payload.string("type")) {
            "device.status" -> {
                val deviceId = payload.string("device_id") ?: return
                val online = payload.boolean("online") ?: false
                val reason = payload.string("reason") ?: ""
                _devices.update { current ->
                    current + (deviceId to DeviceStatus(deviceId, online, reason))
                }
                emit(
                    WsEvent.DeviceStatus(deviceId, online, reason),
                )
            }

            "device.report" -> {
                val deviceId = payload.string("device_id") ?: return
                val report = payload["report"] as? JsonObject ?: JsonObject(emptyMap())
                _devices.update { current ->
                    current + (deviceId to DeviceStatus(deviceId, online = true))
                }
                emit(WsEvent.DeviceReport(deviceId, report))
            }

            "action.result" -> emit(
                WsEvent.ActionResult(
                    deviceId = payload.string("device_id") ?: "",
                    actionId = payload.string("action_id") ?: "",
                    status = payload.string("status") ?: "",
                    detail = payload.string("detail") ?: "",
                    source = payload.string("source") ?: "",
                ),
            )

            "remote.ack" -> emit(
                WsEvent.RemoteAck(
                    requestId = payload.string("request_id") ?: "",
                    ok = payload.boolean("ok") ?: false,
                    error = payload.string("error"),
                    actionId = payload.string("action_id"),
                ),
            )

            "voice.event" -> emit(
                WsEvent.Voice(
                    event = payload.string("event") ?: return,
                    deviceId = payload.string("device_id"),
                    turnId = payload.string("turn_id"),
                    text = payload.string("text"),
                    conversationId = payload.string("conversation_id"),
                    userMessageId = payload.string("user_message_id"),
                    finishReason = payload.string("finish_reason"),
                    interrupted = payload.boolean("interrupted") ?: false,
                ),
            )

            "conversation.turn_state" -> {
                val conversationId = payload.string("conversation_id") ?: return
                val state = payload.string("state") ?: return
                val turnKey = payload.string("turn_key")
                    ?: payload.string("turn_id")
                    ?: return
                val turn = TurnState(
                    conversationId = conversationId,
                    state = state,
                    source = payload.string("source") ?: "app",
                    turnKey = turnKey,
                )
                if (applyTurnState(turn)) {
                    emit(WsEvent.TurnStateChanged(turn))
                }
            }

            "error" -> emit(
                WsEvent.ServerError(
                    code = payload.string("code") ?: "server_error",
                    message = payload.string("message") ?: "服务端错误",
                ),
            )
        }
    }

    /**
     * 返回是否应对外通知。
     *
     * idle 只有在 `turn_key` 与当前活跃轮次一致时才生效；否则是旧轮迟到事件。
     */
    private fun applyTurnState(turn: TurnState): Boolean {
        val current = _turnStates.value[turn.conversationId]
        return when (turn.state) {
            "generating" -> {
                _turnStates.update { it + (turn.conversationId to turn) }
                true
            }

            "idle" -> {
                if (current != null && current.turnKey != turn.turnKey) {
                    false
                } else {
                    _turnStates.update { it - turn.conversationId }
                    true
                }
            }

            else -> false
        }
    }

    private fun emit(event: WsEvent) {
        _events.tryEmit(event)
    }

    private fun JsonObject.string(key: String): String? =
        (this[key] as? JsonPrimitive)?.contentOrNull

    private fun JsonObject.boolean(key: String): Boolean? =
        (this[key] as? JsonPrimitive)?.booleanOrNull

    private fun JsonObject.int(key: String): Int? =
        (this[key] as? JsonPrimitive)?.contentOrNull?.toIntOrNull()

    companion object {
        private const val CLOSE_TOKEN_REVOKED = 4401

        /** 连接存活超过该时长才认为“成功”，接受后立刻关闭不得重置退避。 */
        private const val STABLE_CONNECTION_MILLIS = 30_000L
        private val BACKOFF = longArrayOf(1_000, 2_000, 4_000, 8_000, 16_000, 30_000)

        /** 第 [attempt] 次失败后的等待（0 基），超过上限按 30s。 */
        fun backoffDelay(attempt: Int): Long =
            BACKOFF[attempt.coerceIn(0, BACKOFF.lastIndex)]

        fun wsUrl(serverUrl: String): String {
            val scheme = when (URI(serverUrl).scheme?.lowercase()) {
                "https" -> "wss"
                "http" -> "ws"
                else -> "ws"
            }
            val base = serverUrl.trimEnd('/')
            val withScheme = base.replaceFirst(
                Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://"),
                "$scheme://",
            )
            return "$withScheme/app/ws"
        }
    }
}
