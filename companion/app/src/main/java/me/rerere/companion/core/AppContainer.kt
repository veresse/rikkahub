package me.rerere.companion.core

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import me.rerere.companion.BuildConfig
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.repo.AssistantRepository
import me.rerere.companion.data.repo.AuthRepository
import me.rerere.companion.data.repo.ChatRepository
import me.rerere.companion.data.repo.ConversationRepository
import me.rerere.companion.data.repo.DeviceRepository
import me.rerere.companion.data.repo.HttpAssistantRepository
import me.rerere.companion.data.repo.HttpAuthRepository
import me.rerere.companion.data.repo.HttpChatRepository
import me.rerere.companion.data.repo.HttpConversationRepository
import me.rerere.companion.data.repo.HttpDeviceRepository
import me.rerere.companion.data.repo.HttpMemoryRepository
import me.rerere.companion.data.repo.MemoryRepository
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.KeystoreSecureStore
import me.rerere.companion.data.ws.AppWsManager
import me.rerere.companion.data.ws.OkHttpWsConnector

/**
 * 手写依赖容器。
 *
 * 401 回调通过 `appScope` 走 `SessionManager.invalidate()`，随后 UI 依据
 * `sessionManager.state` 自动回到登录页。所有仓库共享同一个
 * [RetrofitApiFactory]，因此 401 去重与 Token 注入行为一致。
 */
class AppContainer(
    context: Context,
    clock: Clock = Clock { System.currentTimeMillis() },
) {
    private val appContext = context.applicationContext
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val sessionManager = SessionManager(KeystoreSecureStore(appContext), clock)
    val allowCleartext: Boolean = BuildConfig.DEBUG

    private val apiFactory = RetrofitApiFactory(
        sessionProvider = { sessionManager.state.value },
        onUnauthorized = { failedToken ->
            // 只清退“被拒的那个 Token”：旧请求的迟到 401 不能清掉刚登录的新会话。
            appScope.launch { sessionManager.invalidate(failedToken) }
        },
    )

    val authRepository: AuthRepository = HttpAuthRepository(apiFactory, sessionManager)
    val assistantRepository: AssistantRepository =
        HttpAssistantRepository(apiFactory, sessionManager)
    val conversationRepository: ConversationRepository =
        HttpConversationRepository(apiFactory, sessionManager)
    val memoryRepository: MemoryRepository =
        HttpMemoryRepository(apiFactory, sessionManager)
    val chatRepository: ChatRepository =
        HttpChatRepository(apiFactory, sessionManager)
    val deviceRepository: DeviceRepository =
        HttpDeviceRepository(apiFactory, sessionManager)

    val appWsManager: AppWsManager = AppWsManager(
        connector = OkHttpWsConnector(),
        sessionProvider = { sessionManager.state.value },
        onUnauthorized = { failedToken ->
            appScope.launch { sessionManager.invalidate(failedToken) }
        },
        scope = appScope,
    )

    /**
     * 网络可用性：断网时页面必须禁用写操作（刷新/新建/保存/删除/发送）。
     * 回调同时唤醒 WS 重连，避免只能干等退避到点。
     */
    private val connectivityMonitor = ConnectivityMonitor(appContext) {
        appWsManager.onNetworkAvailable()
    }
    val isOnline: StateFlow<Boolean> = connectivityMonitor.online

    init {
        connectivityMonitor.start()
    }

    /**
     * 登出走应用级作用域：UI 协程（如 `rememberCoroutineScope`）在旋转时会被取消，
     * 可能导致本地 Token 没被清掉。
     */
    fun logout() {
        appScope.launch { authRepository.logout() }
    }
}
