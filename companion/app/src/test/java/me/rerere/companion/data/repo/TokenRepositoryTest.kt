package me.rerere.companion.data.repo

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

class TokenRepositoryTest {

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
    fun `list parses token metadata`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """[{"id":"id0","username":"admin","device_name":"客厅平板",
                    "created_at":1700000000000,"last_used_at":1700000001000,
                    "expires_at":1800000000000,"revoked_at":null},
                   {"id":"id1","username":"admin","device_name":"旧手机",
                    "created_at":1600000000000,"last_used_at":null,
                    "expires_at":1700000000000,"revoked_at":1650000000000}]""",
            )
            val session = manager(server.baseUrl)
            val repo = HttpTokenRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            val tokens = (repo.list() as AppResult.Ok).value

            assertEquals(2, tokens.size)
            assertEquals("客厅平板", tokens[0].deviceName)
            assertEquals(1700000000000L, tokens[0].createdAt)
            assertEquals(1700000001000L, tokens[0].lastUsedAt)
            assertNull(tokens[0].revokedAt)
            assertNull(tokens[1].lastUsedAt)
            assertEquals(1650000000000L, tokens[1].revokedAt)
            assertEquals("/api/auth/tokens", server.lastRequestUri)
            assertEquals("GET", server.lastRequestMethod)
        }
    }

    @Test
    fun `revoke posts to token path`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"status":"revoked","token_id":"id1"}""")
            val session = manager(server.baseUrl)
            val repo = HttpTokenRepository(
                RetrofitApiFactory({ session.state.value }, {}),
                session,
            )

            val result = repo.revoke("id1")

            assertTrue(result is AppResult.Ok)
            assertEquals("/api/auth/tokens/id1/revoke", server.lastRequestUri)
            assertEquals("POST", server.lastRequestMethod)
        }
    }
}
