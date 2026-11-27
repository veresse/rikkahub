package me.rerere.companion.data.repo

import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MemoryRepositoryTest {

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

    private fun repository(manager: SessionManager): MemoryRepository =
        HttpMemoryRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)

    @Test
    fun `list sends assistant query and parses content`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """[{"id":"m1","assistant_id":"a1","path":"default.md",
                    "content":"喜欢猫","updated_at":3}]""",
            )
            val result = repository(manager(server.baseUrl)).list("a1")

            assertTrue(result is AppResult.Ok)
            val memory = (result as AppResult.Ok).value.single()
            assertEquals("喜欢猫", memory.content)
            assertEquals("/api/memories?assistant_id=a1", server.lastRequestUri)
        }
    }

    @Test
    fun `create update delete hit expected endpoints`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"id":"m1","status":"created"}""")
            val repo = repository(manager(server.baseUrl))
            val created = repo.create("a1", "新记忆")
            assertTrue(created is AppResult.Ok)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"content\":\"新记忆\""))

            server.enqueue(200, """{"status":"updated"}""")
            val updated = repo.update("m1", "改过的")
            assertTrue(updated is AppResult.Ok)
            assertEquals("PUT", server.lastRequestMethod)
            assertEquals("/api/memories/m1", server.lastRequestUri)

            server.enqueue(200, """{"status":"deleted"}""")
            val deleted = repo.delete("m1")
            assertTrue(deleted is AppResult.Ok)
            assertEquals("DELETE", server.lastRequestMethod)
            assertEquals("/api/memories/m1", server.lastRequestUri)
        }
    }

    @Test
    fun `chinese detail is surfaced as readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(404, """{"detail":"记忆不存在"}""")
            val result = repository(manager(server.baseUrl)).delete("missing")

            assertTrue(result is AppResult.Err)
            val error = (result as AppResult.Err).error as AppError.Http
            assertEquals("记忆不存在", error.message)
        }
    }
}
