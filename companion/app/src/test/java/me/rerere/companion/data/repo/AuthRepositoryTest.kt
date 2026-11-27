package me.rerere.companion.data.repo

import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.AuthApiFactory
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeAuthApi
import me.rerere.companion.testutil.FakeSecureStore
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

class AuthRepositoryTest {

    private fun repository(store: FakeSecureStore, api: FakeAuthApi): Pair<HttpAuthRepository, SessionManager> {
        val manager = SessionManager(store, Clock { 1_000_000L })
        return HttpAuthRepository(AuthApiFactory { api }, manager) to manager
    }

    @Test
    fun `login stores session on success`() = runTest {
        val store = FakeSecureStore()
        val api = FakeAuthApi()
        val (repo, manager) = repository(store, api)

        val result = repo.login("https://example.com/", "admin", "pw")

        assertTrue(result is AppResult.Ok)
        assertEquals("https://example.com/", manager.state.value?.serverUrl)
        assertEquals("t0", manager.state.value?.token)
        assertEquals("admin", api.loginRequests.single().username)
        assertEquals(1, store.writeCount)
    }

    @Test
    fun `login maps http 401 without saving session`() = runTest {
        val store = FakeSecureStore()
        val api = FakeAuthApi {
            throw HttpException(
                Response.error<Any>(
                    401,
                    """{"detail":"invalid_credentials"}""".toResponseBody("application/json".toMediaType()),
                ),
            )
        }
        val (repo, manager) = repository(store, api)

        val result = repo.login("https://example.com/", "admin", "bad")

        assertTrue(result is AppResult.Err)
        assertEquals("invalid_credentials", (result as AppResult.Err).error.code)
        assertNull(manager.state.value)
        assertEquals(0, store.writeCount)
    }

    @Test
    fun `login maps io failure to network error`() = runTest {
        val store = FakeSecureStore()
        val api = FakeAuthApi { throw java.io.IOException("boom") }
        val (repo, manager) = repository(store, api)

        val result = repo.login("https://example.com/", "admin", "pw")

        assertTrue((result as AppResult.Err).error is AppError.Network)
        assertNull(manager.state.value)
    }

    @Test
    fun `logout clears local session even if request fails`() = runTest {
        val store = FakeSecureStore()
        val api = FakeAuthApi()
        val (repo, manager) = repository(store, api)
        repo.login("https://example.com/", "admin", "pw")
        api.logoutError = java.io.IOException("offline")

        repo.logout()

        assertNull(manager.state.value)
        assertNull(store.stored)
        assertEquals(1, api.logoutCalls)
    }

    @Test
    fun `invalid base url maps to readable error`() = runTest {
        val store = FakeSecureStore()
        val manager = SessionManager(store, Clock { 1_000_000L })
        val repo = HttpAuthRepository(
            AuthApiFactory { throw IllegalArgumentException("Invalid URL port") },
            manager,
        )

        val result = repo.login("https://example.com:99999/", "admin", "pw")

        val error = (result as AppResult.Err).error
        assertEquals("invalid_server_url", error.code)
        assertEquals("服务器地址无效", error.message)
        assertNull(manager.state.value)
        assertEquals(0, store.writeCount)
    }
}
