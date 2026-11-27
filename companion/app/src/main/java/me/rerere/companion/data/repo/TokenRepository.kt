package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.AuthTokenDto
import me.rerere.companion.data.session.SessionManager

/** 4D：登录 Token 管理（列表 / 撤销）。 */
interface TokenRepository {
    suspend fun list(): AppResult<List<AuthTokenDto>>

    suspend fun revoke(tokenId: String): AppResult<Unit>
}

class HttpTokenRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), TokenRepository {

    override suspend fun list(): AppResult<List<AuthTokenDto>> = call { it.listTokens() }

    override suspend fun revoke(tokenId: String): AppResult<Unit> = call {
        // 响应只含 status/token_id；调用成功即视为撤销完成。
        it.revokeToken(tokenId)
        Unit
    }
}
