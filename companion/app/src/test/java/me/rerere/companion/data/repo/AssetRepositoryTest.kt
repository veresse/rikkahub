package me.rerere.companion.data.repo

import java.io.ByteArrayInputStream
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AssetRepositoryTest {

    private val viewJson = """{
        "active":{"id":"a1","name":"Q版陪伴","version":"1.0.0","fps":12,
          "frame_count":2,"size_bytes":1024,"sha256":"abc","created_at":1,"activated_at":2},
        "previous":null,
        "fallback":{"name":"占位图","version":"builtin","frame_count":1}
    }"""

    private val uploadJson = """{
        "upload_id":"u1","name":"Q版陪伴","version":"1.0.0","fps":12,
        "frame_count":2,"size_bytes":1024,"sha256":"abc"
    }"""

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

    private fun repo(session: SessionManager) = HttpAssetRepository(
        RetrofitApiFactory({ session.state.value }, {}),
        session,
    )

    @Test
    fun `load parses active previous and fallback`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)

            val view = (repo(session).load() as AppResult.Ok).value

            assertEquals("a1", view.active?.id)
            assertEquals(2, view.active?.frameCount)
            assertNull(view.previous)
            assertEquals("builtin", view.fallback.version)
            assertEquals("/api/assets", server.lastRequestUri)
        }
    }

    @Test
    fun `load allows no active package`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """{"active":null,"previous":null,
                    "fallback":{"name":"占位图","version":"builtin","frame_count":1}}""",
            )
            val session = manager(server.baseUrl)

            val view = (repo(session).load() as AppResult.Ok).value

            assertNull(view.active)
            assertNull(view.previous)
            assertEquals(1, view.fallback.frameCount)
        }
    }

    @Test
    fun `preview streams the zip as multipart`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, uploadJson)
            val session = manager(server.baseUrl)

            val upload = (
                repo(session).preview(
                    ByteArrayInputStream("PK\u0003\u0004asset".toByteArray()),
                    "assets.zip",
                ) as AppResult.Ok
                ).value

            assertEquals("u1", upload.uploadId)
            assertEquals(2, upload.frameCount)
            assertEquals("/api/assets/preview", server.lastRequestUri)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("filename=\"assets.zip\""))
            assertTrue(server.lastRequestBody.contains("PK"))
        }
    }

    @Test
    fun `confirm posts only the upload id`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)

            val view = (repo(session).confirm("u1") as AppResult.Ok).value

            assertEquals("a1", view.active?.id)
            assertEquals("/api/assets/confirm", server.lastRequestUri)
            assertTrue(server.lastRequestBody.contains("\"upload_id\":\"u1\""))
        }
    }

    @Test
    fun `rollback maps 409 to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(409, """{"detail":"no_previous_package"}""")
            val session = manager(server.baseUrl)

            val result = repo(session).rollback()

            assertEquals("/api/assets/rollback", server.lastRequestUri)
            assertEquals(
                "没有可回滚的上一版素材",
                (result as AppResult.Err).error.message,
            )
        }
    }

    @Test
    fun `frame bytes are read from the package id path`() = runTest {
        TestHttpServer().use { server ->
            val payload = ByteArray(2048) { (it % 255).toByte() }
            server.enqueueBytes(200, payload, "image/png")
            val session = manager(server.baseUrl)

            val bytes = (repo(session).frameBytes("a1", 3) as AppResult.Ok).value

            assertEquals(payload.toList(), bytes.toList())
            assertEquals("/api/assets/a1/frame/3", server.lastRequestUri)
        }
    }

    @Test
    fun `oversized frame response is truncated at the cap`() = runTest {
        TestHttpServer().use { server ->
            // 比上限多 100 KiB：不能整包读进内存，最多留到上限 + 1 字节。
            val payload = ByteArray(MAX_ASSET_FRAME_BYTES + 100 * 1024) { 7 }
            server.enqueueBytes(200, payload, "image/png")
            val session = manager(server.baseUrl)

            val bytes = (repo(session).frameBytes("a1", 0) as AppResult.Ok).value

            assertEquals(MAX_ASSET_FRAME_BYTES + 1, bytes.size)
        }
    }
}
