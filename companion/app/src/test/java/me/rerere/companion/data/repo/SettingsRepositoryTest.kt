package me.rerere.companion.data.repo

import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.api.dto.SecretsUpdateBody
import me.rerere.companion.data.api.dto.SettingsUpdateBody
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsRepositoryTest {

    private val viewJson = """{
        "deepseek":{"configured":true,"source":"database",
          "base_url":"https://api.deepseek.com","model":"deepseek-chat"},
        "dashscope":{"configured":false,"source":"unset","region":"cn-beijing",
          "asr_model":"fun-asr-realtime","tts_model":"qwen-tts","tts_voice":"cherry"},
        "voice":{"enabled":true,"provider":"unavailable","effective":"unavailable",
          "reason":"dashscope_key_missing"},
        "search":{"enabled":true,"provider":"builtin_bing","requires_key":false}
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

    @Test
    fun `load parses view and never contains a key`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)
            val repo = HttpSettingsRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            val dto = (repo.load() as AppResult.Ok).value

            assertTrue(dto.deepseek.configured)
            assertEquals("database", dto.deepseek.source)
            assertEquals("https://api.deepseek.com", dto.deepseek.baseUrl)
            assertEquals("dashscope_key_missing", dto.voice.reason)
            assertEquals("builtin_bing", dto.search.provider)
            assertFalse(dto.search.requiresKey)
            assertEquals("/api/settings", server.lastRequestUri)
            assertEquals("GET", server.lastRequestMethod)
        }
    }

    @Test
    fun `save update sends only plain fields`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)
            val repo = HttpSettingsRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            repo.update(
                SettingsUpdateBody(
                    deepseekModel = "deepseek-reasoner",
                    searchEnabled = false,
                ),
            )

            assertEquals("/api/settings", server.lastRequestUri)
            assertEquals("PUT", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"deepseek_model\":\"deepseek-reasoner\""))
            assertTrue(server.lastRequestBody.contains("\"search_enabled\":false"))
            assertFalse(server.lastRequestBody.contains("api_key"))
        }
    }

    @Test
    fun `secret update never echoes the value`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)
            val repo = HttpSettingsRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )
            val secret = "sk-test-write-only"

            val result = repo.updateSecrets(SecretsUpdateBody(deepseekApiKey = secret))

            assertEquals("/api/settings/secrets", server.lastRequestUri)
            assertEquals("PUT", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains(secret))
            assertFalse((result as AppResult.Ok).value.toString().contains(secret))
        }
    }

    @Test
    fun `clear secret hits delete path`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, viewJson)
            val session = manager(server.baseUrl)
            val repo = HttpSettingsRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            val result = repo.clearSecret("dashscope_api_key")

            assertTrue(result is AppResult.Ok)
            assertEquals("DELETE", server.lastRequestMethod)
            assertEquals(
                "/api/settings/secrets/dashscope_api_key",
                server.lastRequestUri,
            )
        }
    }

    @Test
    fun `file_too_large maps to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(413, """{"detail":"file_too_large"}""")
            val session = manager(server.baseUrl)
            val repo = HttpSettingsRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            val result = repo.load()

            assertEquals("文件超过服务器允许的大小", (result as AppResult.Err).error.message)
        }
    }
}
