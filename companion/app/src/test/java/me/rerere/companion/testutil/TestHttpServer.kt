package me.rerere.companion.testutil

import com.sun.net.httpserver.Headers
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress

class TestHttpServer : AutoCloseable {

    private data class Stub(val status: Int, val body: String, val headers: Map<String, String>)

    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    private val stubs = ArrayDeque<Stub>()

    var lastRequestHeaders: Headers? = null
        private set
    var lastRequestBody: String = ""
        private set

    val baseUrl: String
        get() = "http://127.0.0.1:${server.address.port}/"

    init {
        server.createContext("/") { exchange ->
            lastRequestHeaders = exchange.requestHeaders
            lastRequestBody = exchange.requestBody.readBytes().toString(Charsets.UTF_8)
            val stub = if (stubs.isEmpty()) Stub(500, """{"detail":"no_stub"}""", emptyMap()) else stubs.removeFirst()
            stub.headers.forEach { (name, value) -> exchange.responseHeaders.add(name, value) }
            val bytes = stub.body.toByteArray(Charsets.UTF_8)
            exchange.responseHeaders.add("Content-Type", "application/json")
            exchange.sendResponseHeaders(stub.status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        server.start()
    }

    fun enqueue(status: Int, body: String, headers: Map<String, String> = emptyMap()) {
        stubs.addLast(Stub(status, body, headers))
    }

    override fun close() {
        server.stop(0)
    }
}
