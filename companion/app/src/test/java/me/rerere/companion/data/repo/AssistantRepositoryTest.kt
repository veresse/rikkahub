package me.rerere.companion.data.repo

import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AssistantRepositoryTest {

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

    private fun repository(manager: SessionManager): AssistantRepository =
        HttpAssistantRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)

    @Test
    fun `list maps assistants`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """
                [{"id":"a1","name":"夏以昼","system_prompt":"你好","temperature":0.6,
                  "top_p":0.9,"context_message_limit":20,"enable_memory":true,
                  "use_global_memory":false,"enable_web_search":true,
                  "web_search_mode":"on","enable_action_sync":true,
                  "model_id":"deepseek-chat","avatar":{}}]
                """.trimIndent(),
            )
            val items = repository(manager(server.baseUrl)).list()

            assertTrue(items is AppResult.Ok)
            val assistant = (items as AppResult.Ok).value.single()
            assertEquals("a1", assistant.id)
            assertEquals("夏以昼", assistant.name)
            assertEquals("deepseek-chat", assistant.modelId)
            assertEquals("on", assistant.webSearchMode)
            assertEquals(20, assistant.contextMessageLimit)
        }
    }

    @Test
    fun `create sends model and search mode`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"id":"a1","status":"created"}""")
            val result = repository(manager(server.baseUrl)).create(
                AssistantDraft(
                    name = "夏以昼",
                    modelId = "deepseek-chat",
                    webSearchMode = "on",
                ),
            )

            assertTrue(result is AppResult.Ok)
            assertEquals("POST", server.lastRequestMethod)
            assertEquals("/api/assistants", server.lastRequestUri)
            assertTrue(server.lastRequestBody.contains("\"model_id\":\"deepseek-chat\""))
            assertTrue(server.lastRequestBody.contains("\"web_search_mode\":\"on\""))
        }
    }

    @Test
    fun `delete maps 404 detail to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(404, """{"detail":"角色不存在"}""")
            val result = repository(manager(server.baseUrl)).delete("missing")

            assertTrue(result is AppResult.Err)
            val error = (result as AppResult.Err).error as AppError.Http
            assertEquals("角色不存在", error.message)
        }
    }

    @Test
    fun `list maps 401 via error mapper`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(401, """{"detail":"token_revoked"}""")
            val result = repository(manager(server.baseUrl)).list()

            assertTrue(result is AppResult.Err)
            val error = (result as AppResult.Err).error as AppError.Http
            assertEquals("token_revoked", error.code)
            assertEquals("登录已失效，请重新登录", error.message)
        }
    }

    @Test
    fun `no session returns not_logged_in without calling server`() = runTest {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        val result = repository(manager).list()

        assertTrue(result is AppResult.Err)
        assertEquals("not_logged_in", (result as AppResult.Err).error.code)
    }
}
