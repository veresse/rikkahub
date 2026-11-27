package me.rerere.companion.data.repo

import java.io.IOException
import kotlinx.coroutines.CancellationException
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.AuthApiFactory
import me.rerere.companion.data.api.ErrorMapper
import me.rerere.companion.data.api.dto.LoginRequest
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import retrofit2.HttpException

interface AuthRepository {
    suspend fun login(serverUrl: String, username: String, password: String): AppResult<StoredSession>
    suspend fun logout()
}

class HttpAuthRepository(
    private val apiFactory: AuthApiFactory,
    private val sessionManager: SessionManager,
) : AuthRepository {

    override suspend fun login(
        serverUrl: String,
        username: String,
        password: String,
    ): AppResult<StoredSession> {
        // create() 单独包 try：Retrofit 对非法 baseUrl 抛 IllegalArgumentException。
        // 不能把 login() 也包进来——kotlinx 的 SerializationException 同样是 IAE，
        // 会被误报成“服务器地址无效”。
        val api = try {
            apiFactory.create(serverUrl)
        } catch (e: CancellationException) {
            throw e
        } catch (_: IllegalArgumentException) {
            return AppResult.Err(AppError.Http("invalid_server_url", "服务器地址无效"))
        }

        val response = try {
            api.login(LoginRequest(username = username, password = password))
        } catch (e: CancellationException) {
            throw e
        } catch (error: HttpException) {
            val body = error.response()?.errorBody()?.string()
            val retryAfter = error.response()?.headers()?.get("Retry-After")
            return AppResult.Err(ErrorMapper.map(error.code(), body, retryAfter))
        } catch (_: IOException) {
            return AppResult.Err(AppError.Network("无法连接服务器，请检查地址与网络"))
        }

        val session = StoredSession(
            serverUrl = serverUrl,
            token = response.token,
            tokenId = response.tokenId,
            username = response.username,
            expiresAtMillis = response.expiresAt,
        )
        sessionManager.save(session)
        return AppResult.Ok(session)
    }

    override suspend fun logout() {
        val session = sessionManager.state.value
        if (session != null) {
            try {
                apiFactory.create(session.serverUrl).logout()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // 离线或令牌已失效时也必须清空本地会话。
            }
        }
        sessionManager.clear()
    }
}
