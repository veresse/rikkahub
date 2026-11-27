package me.rerere.companion.data.api

import kotlinx.coroutines.runBlocking
import me.rerere.companion.data.api.dto.LoginRequest
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import retrofit2.HttpException

class AuthApiTest {

    private val session = StoredSession(
        serverUrl = "http://127.0.0.1/",
        token = "t0",
        tokenId = "id0",
        username = "admin",
        expiresAtMillis = 2_000_000_000_000,
    )

    @Test
    fun `parses login response`() = runBlocking {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """{"token":"t0","token_id":"id0","username":"admin","expires_at":1700000000000}""",
            )
            val api = RetrofitApiFactory({ null }, {}).create(server.baseUrl)

            val response = api.login(LoginRequest(username = "admin", password = "pw"))

            assertEquals("t0", response.token)
            assertEquals("id0", response.tokenId)
            assertEquals(1_700_000_000_000, response.expiresAt)
            assertTrue(server.lastRequestBody.contains("\"device_name\":\"android\""))
        }
    }

    @Test
    fun `adds bearer token to authenticated requests`() = runBlocking {
        TestHttpServer().use { server ->
            server.enqueue(200, "{}")
            val api = RetrofitApiFactory({ session }, {}).create(server.baseUrl)

            api.logout()

            assertEquals("Bearer t0", server.lastRequestHeaders?.getFirst("Authorization"))
        }
    }

    @Test
    fun `triggers unauthorized once for repeated 401`() = runBlocking {
        TestHttpServer().use { server ->
            server.enqueue(401, """{"detail":"token_revoked"}""")
            server.enqueue(401, """{"detail":"token_revoked"}""")
            var notified = 0
            val api = RetrofitApiFactory({ session }, { notified += 1 }).create(server.baseUrl)

            repeat(2) {
                try {
                    api.logout()
                    fail("expected HttpException")
                } catch (_: HttpException) {
                    // expected
                }
            }

            assertEquals(1, notified)
        }
    }

    @Test
    fun `does not treat login 401 as session expiry`() = runBlocking {
        TestHttpServer().use { server ->
            server.enqueue(401, """{"detail":"invalid_credentials"}""")
            var notified = 0
            val api = RetrofitApiFactory({ session }, { notified += 1 }).create(server.baseUrl)

            try {
                api.login(LoginRequest(username = "admin", password = "bad"))
                fail("expected HttpException")
            } catch (error: HttpException) {
                assertNotNull(error.response()?.errorBody())
            }

            assertEquals(0, notified)
        }
    }

    @Test
    fun `notifies again when a new token is rejected`() = runBlocking {
        TestHttpServer().use { server ->
            var current = session
            val notified = mutableListOf<String>()
            val factory = RetrofitApiFactory({ current }, { notified += it })

            server.enqueue(401, """{"detail":"token_revoked"}""")
            try {
                factory.create(server.baseUrl).logout()
                fail("expected HttpException")
            } catch (_: HttpException) {
            }

            current = session.copy(token = "t1")
            server.enqueue(401, """{"detail":"token_revoked"}""")
            try {
                factory.create(server.baseUrl).logout()
                fail("expected HttpException")
            } catch (_: HttpException) {
            }

            // 同一工厂的 Token 变化必须重新获得一次清退机会，否则二次登录后 401 会“失聪”。
            assertEquals(listOf("t0", "t1"), notified)
        }
    }
}
