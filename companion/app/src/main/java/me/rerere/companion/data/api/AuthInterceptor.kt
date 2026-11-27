package me.rerere.companion.data.api

import java.util.concurrent.atomic.AtomicReference
import okhttp3.Interceptor
import okhttp3.Response

/**
 * 给非登录请求注入 Bearer Token，并在“某个 Token 第一次被 401 拒绝”时回调一次。
 *
 * 只有同时满足四个条件才回调：
 * 1. 不是登录请求（登录返回 401 是口令错误）；
 * 2. 实际发出的请求带 `Authorization: Bearer`；
 * 3. 该请求有 Token；
 * 4. 这个 Token 与上次已通知的不同（[notifiedToken] 记录的是 Token 而不是布尔值）。
 *
 * 用 Token 而不是 `Boolean`：工厂与 `AppContainer` 同生命周期，布尔标志一旦置真就
 * 永不复位，重新登录后的新 Token 再失效时不会清退。Token 变化即自然视为新会话。
 */
class AuthInterceptor(
    private val tokenProvider: () -> String?,
    private val isLoginRequest: (encodedPath: String) -> Boolean,
    private val notifiedToken: AtomicReference<String?>,
    private val onUnauthorized: (token: String) -> Unit,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val loginRequest = isLoginRequest(request.url.encodedPath)
        val token = tokenProvider()
        val outgoing = if (!loginRequest && token != null) {
            request.newBuilder().header("Authorization", "Bearer $token").build()
        } else {
            request
        }

        val response = chain.proceed(outgoing)
        val carriesBearer = outgoing.header("Authorization")?.startsWith("Bearer ") == true
        if (response.code == 401 && !loginRequest && carriesBearer && token != null &&
            notifiedToken.getAndSet(token) != token
        ) {
            onUnauthorized(token)
        }
        return response
    }
}
