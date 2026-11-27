package me.rerere.companion.testutil

import com.sun.net.httpserver.Headers
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress

class TestHttpServer : AutoCloseable {

    private data class Stub(
        val status: Int,
        val bytes: ByteArray,
        val contentType: String,
        val headers: Map<String, String>,
    )

    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    private val stubs = ArrayDeque<Stub>()

    var lastRequestHeaders: Headers? = null
        private set
    var lastRequestBodyBytes: ByteArray = ByteArray(0)
        private set
    var lastRequestMethod: String = ""
        private set
    var lastRequestUri: String = ""
        private set

    /** 文本视图：JSON/表单用例沿用旧断言；二进制请读 [lastRequestBodyBytes]。 */
    val lastRequestBody: String
        get() = lastRequestBodyBytes.toString(Charsets.UTF_8)

    val baseUrl: String
        get() = "http://127.0.0.1:${server.address.port}/"

    init {
        server.createContext("/") { exchange ->
            lastRequestHeaders = exchange.requestHeaders
            lastRequestMethod = exchange.requestMethod
            lastRequestUri = exchange.requestURI.toString()
            lastRequestBodyBytes = exchange.requestBody.readBytes()
            val stub = if (stubs.isEmpty()) {
                Stub(500, """{"detail":"no_stub"}""".toByteArray(), "application/json", emptyMap())
            } else {
                stubs.removeFirst()
            }
            stub.headers.forEach { (name, value) -> exchange.responseHeaders.add(name, value) }
            exchange.responseHeaders.add("Content-Type", stub.contentType)
            exchange.sendResponseHeaders(stub.status, stub.bytes.size.toLong())
            exchange.responseBody.use { it.write(stub.bytes) }
        }
        server.start()
    }

    fun enqueue(status: Int, body: String, headers: Map<String, String> = emptyMap()) {
        enqueueBytes(status, body.toByteArray(Charsets.UTF_8), "application/json", headers)
    }

    fun enqueueBytes(
        status: Int,
        bytes: ByteArray,
        contentType: String = "application/octet-stream",
        headers: Map<String, String> = emptyMap(),
    ) {
        stubs.addLast(Stub(status, bytes, contentType, headers))
    }

    override fun close() {
        server.stop(0)
    }
}
