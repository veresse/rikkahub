package me.rerere.companion.data.repo

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.api.dto.ChatRequestDto
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.data.sse.ChatEvent
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import com.sun.net.httpserver.HttpServer
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatRepositoryTest {

    private suspend fun manager(url: String): SessionManager {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        manager.save(
            StoredSession(
                serverUrl = url,
                token = "t0",
                tokenId = "id0",
                username = "admin",
                expiresAtMillis = Long.MAX_VALUE,
            ),
        )
        return manager
    }

    private fun repository(manager: SessionManager): ChatRepository =
        HttpChatRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)

    private val request = ChatRequestDto(
        conversationId = "c1",
        message = "在吗",
        clientMsgId = "m-1",
    )

    @Test
    fun `streams content then done with ids`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                "data: {\"type\":\"content\",\"delta\":\"在的\"}\n\n" +
                    "data: {\"type\":\"done\",\"finish_reason\":\"stop\"," +
                    "\"assistant_message_id\":\"m1\",\"user_message_id\":\"u1\",\"replayed\":false}\n\n",
            )
            val events = repository(manager(server.baseUrl)).stream(request).toList()

            assertEquals(2, events.size)
            assertEquals(ChatEvent.Content("在的"), events[0])
            val done = events[1] as ChatEvent.Done
            assertEquals("m1", done.assistantMessageId)
            assertEquals("u1", done.userMessageId)
            assertEquals("POST", server.lastRequestMethod)
            assertEquals("/api/chat/completions", server.lastRequestUri)
            assertTrue(server.lastRequestBody.contains("\"client_msg_id\":\"m-1\""))
        }
    }

    @Test
    fun `replayed done is surfaced`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, "data: {\"type\":\"done\",\"replayed\":true}\n\n")
            val events = repository(manager(server.baseUrl)).stream(request).toList()

            val done = events.single() as ChatEvent.Done
            assertTrue(done.replayed)
        }
    }

    @Test
    fun `cancelling the flow stops reading`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                "data: {\"type\":\"content\",\"delta\":\"一\"}\n\n" +
                    "data: {\"type\":\"content\",\"delta\":\"二\"}\n\n",
            )
            val events = repository(manager(server.baseUrl)).stream(request).take(1).toList()

            assertEquals(listOf(ChatEvent.Content("一")), events)
        }
    }

    @Test
    fun `http error becomes failed with mapped message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(401, """{"detail":"token_revoked"}""")
            val events = repository(manager(server.baseUrl)).stream(request).toList()

            val failed = events.single() as ChatEvent.Failed
            assertEquals("token_revoked", failed.code)
            assertEquals("登录已失效，请重新登录", failed.message)
        }
    }

    @Test
    fun `429 carries retry after seconds`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                429,
                """{"detail":"rate_limited"}""",
                mapOf("Retry-After" to "7"),
            )
            val events = repository(manager(server.baseUrl)).stream(request).toList()

            val failed = events.single() as ChatEvent.Failed
            assertEquals("rate_limited", failed.code)
            assertEquals(7, failed.retryAfterSeconds)
        }
    }

    @Test
    fun `no session reports not logged in`() = runTest {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        val events = repository(manager).stream(request).toList()

        assertEquals("not_logged_in", (events.single() as ChatEvent.Failed).code)
    }

    @Test
    fun `stream ending without done becomes failure`() = runTest {
        TestHttpServer().use { server ->
            // 服务端崩溃/代理截断：只有半帧正文，没有 done/error。
            server.enqueue(200, "data: {\"type\":\"content\",\"delta\":\"一\"}\n\n")
            val events = repository(manager(server.baseUrl)).stream(request).toList()

            assertEquals(ChatEvent.Content("一"), events.first())
            assertEquals("stream_closed", (events.last() as ChatEvent.Failed).code)
        }
    }

    @Test
    fun `io error after done does not become failure`() = runBlocking {
        val body = "data: {\"type\":\"done\",\"finish_reason\":\"stop\"," +
            "\"assistant_message_id\":\"m1\",\"user_message_id\":\"u1\",\"replayed\":false}\n\n"
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val worker = thread(isDaemon = true) {
            runCatching {
                server.accept().use { socket ->
                    val reader = socket.getInputStream().bufferedReader()
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                    }
                    val out = socket.getOutputStream()
                    out.write(
                        (
                            "HTTP/1.1 200 OK\r\n" +
                                "Content-Type: text/event-stream\r\n" +
                                "Transfer-Encoding: chunked\r\n" +
                                "Connection: close\r\n\r\n"
                            ).toByteArray(),
                    )
                    val chunk = body.toByteArray()
                    out.write("${Integer.toHexString(chunk.size)}\r\n".toByteArray())
                    out.write(chunk)
                    out.write("\r\n".toByteArray())
                    out.flush()
                    // 故意不写 chunked 终止块，直接断开：模拟 done 之后代理 RST。
                }
            }
        }
        try {
            val url = "http://127.0.0.1:${server.localPort}/"
            val events = repository(manager(url)).stream(request).toList()

            // 终帧之后的 IO 异常不得追加 stream_closed/失败，完整回答不能被覆盖。
            assertEquals(1, events.size)
            assertTrue(events.single() is ChatEvent.Done)
        } finally {
            server.close()
            worker.join(5_000)
        }
    }

    @Test
    fun `cancelling the flow closes the socket so the server stops`() = runBlocking {
        val firstFrameSent = CountDownLatch(1)
        val serverSawDisconnect = CountDownLatch(1)
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange ->
            exchange.requestBody.readBytes()
            exchange.responseHeaders.add("Content-Type", "text/event-stream")
            // 0 = chunked：写完首帧后持续挂着，模拟长时间生成。
            exchange.sendResponseHeaders(200, 0)
            val out = exchange.responseBody
            out.write("data: {\"type\":\"content\",\"delta\":\"一\"}\n\n".toByteArray())
            out.flush()
            firstFrameSent.countDown()
            try {
                while (true) {
                    Thread.sleep(20)
                    out.write(": ping\n\n".toByteArray())
                    out.flush()
                }
            } catch (_: IOException) {
                serverSawDisconnect.countDown()
            } finally {
                runCatching { out.close() }
            }
        }
        server.start()
        try {
            val manager = manager("http://127.0.0.1:${server.address.port}/")
            val job = launch(Dispatchers.IO) {
                repository(manager).stream(request).collect { }
            }
            assertTrue("首帧未到达", firstFrameSent.await(5, TimeUnit.SECONDS))
            job.cancelAndJoin()
            assertTrue(
                "取消后客户端未断开连接（服务端仍在写）",
                serverSawDisconnect.await(5, TimeUnit.SECONDS),
            )
        } finally {
            server.stop(0)
        }
    }
}
