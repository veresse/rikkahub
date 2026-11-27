package me.rerere.companion.data.repo

import java.io.IOException
import kotlinx.coroutines.CancellationException
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApi
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.ErrorMapper
import me.rerere.companion.data.session.SessionManager
import retrofit2.HttpException

/**
 * 需要登录态的仓库基类：统一处理“未登录 / HTTP 错误 / 网络错误”。
 *
 * UI 层不拼 URL、不解析错误体；所有错误都变成可读的 [AppError]。
 */
abstract class SessionApi(
    private val apiFactory: CompanionApiFactory,
    private val sessionManager: SessionManager,
) {

    protected suspend fun <T> call(block: suspend (CompanionApi) -> T): AppResult<T> {
        val session = sessionManager.state.value
            ?: return AppResult.Err(AppError.Http("not_logged_in", "请先登录"))
        return try {
            AppResult.Ok(block(apiFactory.createCompanion(session.serverUrl)))
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            val body = runCatching { e.response()?.errorBody()?.string() }.getOrNull()
            val retryAfter = e.response()?.headers()?.get("Retry-After")
            AppResult.Err(ErrorMapper.map(e.code(), body, retryAfter))
        } catch (_: IOException) {
            AppResult.Err(AppError.Network("无法连接服务器，请检查网络"))
        } catch (_: Exception) {
            // 字段漂移/缺字段（SerializationException）、非法 baseUrl 等不能击穿到 UI。
            AppResult.Err(AppError.Http("bad_response", "服务器返回了无法识别的数据"))
        }
    }
}
