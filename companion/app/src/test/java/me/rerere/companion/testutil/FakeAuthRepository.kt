package me.rerere.companion.testutil

import kotlinx.coroutines.CompletableDeferred
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.repo.AuthRepository
import me.rerere.companion.data.store.StoredSession

class FakeAuthRepository(
    var result: AppResult<StoredSession> = AppResult.Ok(
        StoredSession(
            serverUrl = "https://example.com/",
            token = "t0",
            tokenId = "id0",
            username = "admin",
            expiresAtMillis = 2_000_000_000_000,
        ),
    ),
) : AuthRepository {

    data class Call(val serverUrl: String, val username: String, val password: String)

    val calls = mutableListOf<Call>()
    var gate: CompletableDeferred<Unit>? = null
    var logoutCalls: Int = 0
        private set

    override suspend fun login(
        serverUrl: String,
        username: String,
        password: String,
    ): AppResult<StoredSession> {
        calls += Call(serverUrl, username, password)
        gate?.await()
        return result
    }

    override suspend fun logout() {
        logoutCalls += 1
    }
}
