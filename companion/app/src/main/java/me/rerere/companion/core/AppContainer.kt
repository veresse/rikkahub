package me.rerere.companion.core

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import me.rerere.companion.BuildConfig
import me.rerere.companion.data.api.RetrofitAuthApiFactory
import me.rerere.companion.data.repo.AuthRepository
import me.rerere.companion.data.repo.HttpAuthRepository
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.KeystoreSecureStore

/**
 * 手写依赖容器（4A 只有登录链路，后续按块追加 Repository）。
 *
 * 401 回调通过 `appScope` 走 `SessionManager.invalidate()`，随后 UI 依据
 * `sessionManager.state` 自动回到登录页。
 */
class AppContainer(
    context: Context,
    clock: Clock = Clock { System.currentTimeMillis() },
) {
    private val appContext = context.applicationContext
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val sessionManager = SessionManager(KeystoreSecureStore(appContext), clock)
    val allowCleartext: Boolean = BuildConfig.DEBUG

    val authRepository: AuthRepository = HttpAuthRepository(
        RetrofitAuthApiFactory(
            sessionProvider = { sessionManager.state.value },
            onUnauthorized = { failedToken ->
                // 只清退“被拒的那个 Token”：旧请求的迟到 401 不能清掉刚登录的新会话。
                appScope.launch { sessionManager.invalidate(failedToken) }
            },
        ),
        sessionManager,
    )

    /**
     * 登出走应用级作用域：UI 协程（如 `rememberCoroutineScope`）在旋转时会被取消，
     * 可能导致本地 Token 没被清掉。
     */
    fun logout() {
        appScope.launch { authRepository.logout() }
    }
}
