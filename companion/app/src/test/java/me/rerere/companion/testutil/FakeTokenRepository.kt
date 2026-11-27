package me.rerere.companion.testutil

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AuthTokenDto
import me.rerere.companion.data.repo.TokenRepository

class FakeTokenRepository : TokenRepository {
    var tokens: List<AuthTokenDto> = emptyList()

    var listResult: AppResult<List<AuthTokenDto>>? = null
    var revokeResult: AppResult<Unit>? = null

    var listCalls: Int = 0
    val revokeCalls: MutableList<String> = mutableListOf()

    override suspend fun list(): AppResult<List<AuthTokenDto>> {
        listCalls += 1
        return listResult ?: AppResult.Ok(tokens)
    }

    override suspend fun revoke(tokenId: String): AppResult<Unit> {
        revokeCalls += tokenId
        return revokeResult ?: AppResult.Ok(Unit)
    }
}
