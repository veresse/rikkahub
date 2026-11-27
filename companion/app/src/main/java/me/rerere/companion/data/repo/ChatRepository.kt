package me.rerere.companion.data.repo

import java.io.IOException
import java.net.SocketTimeoutException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.job
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.ErrorMapper
import me.rerere.companion.data.api.dto.ChatRequestDto
import me.rerere.companion.data.api.dto.MessageTextUpdate
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.sse.ChatEvent
import me.rerere.companion.data.sse.SseParser
import retrofit2.HttpException

interface ChatRepository {
    fun stream(request: ChatRequestDto): Flow<ChatEvent>
    suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit>
}

/**
 * SSE 聊天仓库：逐行读流并在同一协程里解析。
 *
 * - 取消（停止生成 / 退出页面）必须真正断开连接：协程取消本身不会打断阻塞中的
 *   `readUtf8Line()`，所以通过 `Call.cancel()` 关闭 socket，服务端才能感知断连并
 *   停止生成（否则会白烧 token 并占着会话锁）。
 * - 读超时由流式 OkHttpClient 提供（120s 无任何字节），避免模型挂起时无限转圈。
 * - 所有 HTTP/网络错误都转成单条 [ChatEvent.Failed]，不抛给 UI；流在没有
 *   `done`/`error` 的情况下 EOF 也补一条失败，消费方不必区分“截断”和“完成”。
 * - 日志与 UI 都不接触原始请求体。
 */
class HttpChatRepository(
    private val apiFactory: CompanionApiFactory,
    private val sessionManager: SessionManager,
) : ChatRepository {

    override fun stream(request: ChatRequestDto): Flow<ChatEvent> = flow {
        val session = sessionManager.state.value
        if (session == null) {
            emit(ChatEvent.Failed("not_logged_in", "请先登录"))
            return@flow
        }

        val call = apiFactory.createStreaming(session.serverUrl).chatCall(request)
        currentCoroutineContext().job.invokeOnCompletion { call.cancel() }

        val response = try {
            call.execute()
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            emit(httpFailure(e))
            return@flow
        } catch (e: IOException) {
            emit(ChatEvent.Failed("network", "无法连接服务器，请检查网络"))
            return@flow
        }

        if (!response.isSuccessful) {
            val errorBody = response.errorBody()
            val raw = runCatching { errorBody?.string() }.getOrNull()
            errorBody?.close()
            emit(httpFailure(response.code(), raw, response.headers()["Retry-After"]))
            return@flow
        }

        val body = response.body()
        if (body == null) {
            emit(ChatEvent.Failed("bad_response", "服务器返回了空响应"))
            return@flow
        }

        var terminal = false
        val parser = SseParser()

        suspend fun publish(events: List<ChatEvent>) {
            for (event in events) {
                if (event is ChatEvent.Done || event is ChatEvent.Failed) terminal = true
                emit(event)
            }
        }

        body.use { responseBody ->
            val source = responseBody.source()
            try {
                while (currentCoroutineContext().isActive) {
                    val line = source.readUtf8Line() ?: break
                    publish(parser.feed(line + "\n"))
                }
                publish(parser.finish())
            } catch (e: CancellationException) {
                throw e
            } catch (e: IOException) {
                // 取消触发的 socket 关闭不是错误，交给调用方静默结束。
                currentCoroutineContext().ensureActive()
                // 已经收到 done/error 后的 close/RST 不得覆盖成功结果。
                if (!terminal) {
                    emit(ioFailure(e))
                    terminal = true
                }
            }
        }
        if (!terminal) {
            emit(ChatEvent.Failed("stream_closed", "连接中断，请重试"))
        }
    }.flowOn(Dispatchers.IO)

    override suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit> {
        val session = sessionManager.state.value
            ?: return AppResult.Err(AppError.Http("not_logged_in", "请先登录"))
        return try {
            apiFactory.createCompanion(session.serverUrl)
                .correctMessage(
                    conversationId,
                    nodeId,
                    messageId,
                    MessageTextUpdate(text),
                )
            AppResult.Ok(Unit)
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            val raw = runCatching { e.response()?.errorBody()?.string() }.getOrNull()
            AppResult.Err(ErrorMapper.map(e.code(), raw, e.response()?.headers()?.get("Retry-After")))
        } catch (_: IOException) {
            AppResult.Err(AppError.Network("无法连接服务器，请检查网络"))
        } catch (_: Exception) {
            AppResult.Err(AppError.Http("bad_response", "服务器返回了无法识别的数据"))
        }
    }

    private fun httpFailure(e: HttpException): ChatEvent.Failed = httpFailure(
        code = e.code(),
        raw = runCatching { e.response()?.errorBody()?.string() }.getOrNull(),
        retryAfter = e.response()?.headers()?.get("Retry-After"),
    )


    private fun httpFailure(code: Int, raw: String?, retryAfter: String?): ChatEvent.Failed {
        val error = ErrorMapper.map(code, raw, retryAfter)
        return ChatEvent.Failed(
            code = error.code,
            message = error.message,
            retryAfterSeconds = (error as? AppError.Http)?.retryAfterSeconds,
        )
    }

    private fun ioFailure(e: IOException): ChatEvent.Failed = if (e is SocketTimeoutException) {
        ChatEvent.Failed("stream_timeout", "服务器长时间无响应，请重试")
    } else {
        ChatEvent.Failed("network", "连接中断，请重试")
    }
}
