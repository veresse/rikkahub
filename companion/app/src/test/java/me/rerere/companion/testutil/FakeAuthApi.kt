package me.rerere.companion.testutil

import me.rerere.companion.data.api.AuthApi
import me.rerere.companion.data.api.dto.LoginRequest
import me.rerere.companion.data.api.dto.LoginResponse

class FakeAuthApi(
    private val onLogin: (LoginRequest) -> LoginResponse = {
        LoginResponse(token = "t0", tokenId = "id0", username = it.username, expiresAt = 2_000_000_000_000)
    },
) : AuthApi {
    val loginRequests = mutableListOf<LoginRequest>()
    var logoutCalls: Int = 0
        private set
    var logoutError: Exception? = null

    override suspend fun login(body: LoginRequest): LoginResponse {
        loginRequests += body
        return onLogin(body)
    }

    override suspend fun logout() {
        logoutCalls += 1
        logoutError?.let { throw it }
    }
}
