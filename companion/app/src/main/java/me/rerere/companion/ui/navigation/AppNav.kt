package me.rerere.companion.ui.navigation

import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import kotlinx.coroutines.delay
import me.rerere.companion.core.AppContainer
import me.rerere.companion.feature.assistants.AssistantEditEvent
import me.rerere.companion.feature.assistants.AssistantEditScreen
import me.rerere.companion.feature.assistants.AssistantEditViewModel
import me.rerere.companion.feature.assistants.AssistantsScreen
import me.rerere.companion.feature.assistants.AssistantsViewModel
import me.rerere.companion.feature.chat.ChatScreen
import me.rerere.companion.feature.chat.ChatViewModel
import me.rerere.companion.feature.chat.ConversationsEvent
import me.rerere.companion.feature.chat.ConversationsScreen
import me.rerere.companion.feature.chat.ConversationsViewModel
import me.rerere.companion.feature.chat.MemoriesScreen
import me.rerere.companion.feature.chat.MemoriesViewModel
import me.rerere.companion.feature.login.LoginScreen
import me.rerere.companion.feature.login.LoginViewModel

/** 返回节流窗口：同一次连点不弹两级，也不在弹回根页后立刻退出应用。 */
private const val ROOT_BACK_BLOCK_MILLIS = 400L

/**
 * 手写导航栈：登录态由 `SessionManager` 决定，登录后的页面用
 * `mutableStateListOf<AppRoute>` 管理，系统返回键弹栈。
 *
 * 不引入 navigation-compose：4B 的页面层级只有三层，手写栈更容易测试
 * 登录态切换时的重置行为。
 */
@Composable
fun AppNav(container: AppContainer) {
    val session by container.sessionManager.state.collectAsStateWithLifecycle()

    LaunchedEffect(container) {
        container.sessionManager.restore()
    }

    val current = session
    LaunchedEffect(current?.token) {
        if (current != null) container.appWsManager.start() else container.appWsManager.stop()
    }

    // 手写栈的页面 ViewModel 都存在这里；登出时必须全部清掉，
    // 否则聊天页的 SSE/WS 收集器会在登录页继续跑。
    val navigationViewModel: NavigationViewModel = viewModel()
    LaunchedEffect(current?.tokenId) {
        if (current == null) navigationViewModel.clearAll()
    }

    if (current == null) {
        LoginRoute(container)
        return
    }

    // 注意：这两个 rememberSaveable 依赖位置作用域（自定义 key 已被 Compose 废弃）。
    // 新的 rememberSaveable 只能追加在它们之后，否则旋转恢复会错位。
    val backStack = rememberSaveable(saver = AppRouteBackStackSaver) {
        mutableStateListOf<AppRoute>(AppRoute.Assistants)
    }
    val online by container.isOnline.collectAsStateWithLifecycle()

    // 连点返回会一次弹两级（第二次点击落在上一页的返回按钮上），给弹栈加个短节流。
    var lastPopAt by remember { mutableStateOf(0L) }
    // 弹回根页后的短暂窗口内继续吞掉返回：否则深度 2 的第二下会直接结束
    // Activity，清空 ViewModelStore 并取消在途写请求。窗口过后恢复系统默认退出。
    var blockingRootBack by remember { mutableStateOf(false) }
    LaunchedEffect(blockingRootBack) {
        if (!blockingRootBack) return@LaunchedEffect
        delay(ROOT_BACK_BLOCK_MILLIS)
        blockingRootBack = false
    }

    fun pushRoute(route: AppRoute) {
        // 双击卡片会压入两个相同页面，返回一次看起来“没反应”。
        if (backStack.lastOrNull() != route) {
            // 新页面不继承上一页的返回节流，否则“弹栈→立刻进入→立刻返回”会被吞掉。
            lastPopAt = 0L
            backStack.add(route)
        }
    }

    fun popRoute() {
        if (backStack.size <= 1) return
        val now = System.currentTimeMillis()
        if (now - lastPopAt < ROOT_BACK_BLOCK_MILLIS) return
        lastPopAt = now
        val removed = backStack.removeAt(backStack.lastIndex)
        if (backStack.size <= 1) blockingRootBack = true
        // 弹栈即销毁该页 ViewModel：取消 SSE、停止收集 WS，重进时重新拉详情。
        navigationViewModel.clearRoute(removed)
    }

    // 重新登录/切换 Token 时回到角色列表，避免看到上一个账号的页面。
    // 用 rememberSaveable 记录上次 Token：旋转重建时不能把栈和正在生成的聊天重置。
    var activeTokenId by rememberSaveable { mutableStateOf(current.tokenId) }
    LaunchedEffect(current.tokenId) {
        if (activeTokenId == current.tokenId) return@LaunchedEffect
        activeTokenId = current.tokenId
        navigationViewModel.clearAll()
        backStack.clear()
        backStack.add(AppRoute.Assistants)
    }

    BackHandler(enabled = backStack.size > 1) {
        popRoute()
    }
    BackHandler(enabled = blockingRootBack) { }

    val assistantsOwner = navigationViewModel.ownerFor(AppRoute.Assistants)
    val assistantsViewModel: AssistantsViewModel = viewModel(
        viewModelStoreOwner = assistantsOwner,
        key = "assistants",
        factory = viewModelFactory {
            initializer { AssistantsViewModel(container.assistantRepository) }
        },
    )

    val route = backStack.lastOrNull() ?: return
    when (route) {
        AppRoute.Assistants -> {
            val state by assistantsViewModel.state.collectAsStateWithLifecycle()
            // 根页面删除角色时禁止返回：系统返回会结束 Activity 并取消 DELETE。
            BackHandler(enabled = state.deletingId != null) { }
            AssistantsScreen(
                state = state,
                events = assistantsViewModel.events,
                online = online,
                onRefresh = assistantsViewModel::refresh,
                onCreate = { pushRoute(AppRoute.AssistantEdit(assistantId = null)) },
                onOpen = { assistant ->
                    pushRoute(AppRoute.Conversations(assistant.id, assistant.name))
                },
                onEdit = { id -> pushRoute(AppRoute.AssistantEdit(assistantId = id)) },
                onDelete = assistantsViewModel::delete,
                onLogout = container::logout,
            )
        }

        is AppRoute.AssistantEdit -> {
            val editViewModel: AssistantEditViewModel = viewModel(
                viewModelStoreOwner = navigationViewModel.ownerFor(route),
                key = "assistant-edit-${route.instanceId}",
                factory = viewModelFactory {
                    initializer {
                        AssistantEditViewModel(container.assistantRepository, route.assistantId)
                    }
                },
            )
            val state by editViewModel.state.collectAsStateWithLifecycle()
            // 始终接管返回，回调里读最新状态：collectAsState 的值落后一帧，
            // 用它做 enabled 会留下“保存已开始但仍能弹栈”的窗口。
            BackHandler {
                if (editViewModel.state.value.saving) return@BackHandler
                if (backStack.lastOrNull() == route) popRoute()
            }
            LaunchedEffect(editViewModel) {
                editViewModel.events.collect { event ->
                    if (event is AssistantEditEvent.Saved) {
                        // 保存与返回键可能同时发生：只弹自己，绝不把角色列表也弹掉。
                        if (backStack.lastOrNull() == route) {
                            popRoute()
                        }
                        assistantsViewModel.refresh()
                    }
                }
            }
            AssistantEditScreen(
                state = state,
                events = editViewModel.events,
                online = online,
                onUpdate = editViewModel::update,
                onSave = editViewModel::save,
                onRetryLoad = editViewModel::retryLoad,
                onBack = {
                    if (!editViewModel.state.value.saving &&
                        backStack.lastOrNull() == route
                    ) {
                        popRoute()
                    }
                },
            )
        }

        is AppRoute.Conversations -> {
            val conversationsViewModel: ConversationsViewModel = viewModel(
                viewModelStoreOwner = navigationViewModel.ownerFor(route),
                key = "conversations-${route.assistantId}",
                factory = viewModelFactory {
                    initializer {
                        ConversationsViewModel(
                            repository = container.conversationRepository,
                            assistantId = route.assistantId,
                            autoLoad = false,
                        )
                    }
                },
            )
            val state by conversationsViewModel.state.collectAsStateWithLifecycle()
            // 从聊天页返回时 Route 会重新进入组合，这里刷新标题/排序。
            LaunchedEffect(Unit) { conversationsViewModel.refresh() }
            // 新建/删除在途时禁止返回，避免取消写请求后状态不确定。
            BackHandler {
                val latest = conversationsViewModel.state.value
                if (latest.creating || latest.deletingId != null) return@BackHandler
                if (backStack.lastOrNull() == route) popRoute()
            }
            val turnStates by container.appWsManager.turnStates.collectAsStateWithLifecycle()
            val generatingIds = remember(turnStates) {
                turnStates.values.filter { it.state == "generating" }
                    .mapTo(mutableSetOf()) { it.conversationId }
            }
            LaunchedEffect(conversationsViewModel) {
                conversationsViewModel.events.collect { event ->
                    if (event is ConversationsEvent.Created && backStack.lastOrNull() == route) {
                        pushRoute(
                            AppRoute.Chat(
                                conversationId = event.conversationId,
                                assistantId = route.assistantId,
                                assistantName = route.assistantName,
                            ),
                        )
                    }
                }
            }
            ConversationsScreen(
                assistantName = route.assistantName,
                state = state,
                events = conversationsViewModel.events,
                online = online,
                generatingIds = generatingIds,
                onRefresh = conversationsViewModel::refresh,
                onCreate = conversationsViewModel::create,
                onOpen = { conversation ->
                    pushRoute(
                        AppRoute.Chat(
                            conversationId = conversation.id,
                            assistantId = route.assistantId,
                            assistantName = route.assistantName,
                        ),
                    )
                },
                onDelete = conversationsViewModel::delete,
                onMemories = {
                    pushRoute(
                        AppRoute.Memories(
                            assistantId = route.assistantId,
                            assistantName = route.assistantName,
                        ),
                    )
                },
                onDismissSnackbar = conversationsViewModel::dismissSnackbar,
                onBack = {
                    val latest = conversationsViewModel.state.value
                    if (!latest.creating && latest.deletingId == null &&
                        backStack.lastOrNull() == route
                    ) {
                        popRoute()
                    }
                },
            )
        }

        is AppRoute.Chat -> {
            val chatViewModel: ChatViewModel = viewModel(
                viewModelStoreOwner = navigationViewModel.ownerFor(route),
                key = "chat-${route.conversationId}",
                factory = viewModelFactory {
                    initializer {
                        ChatViewModel(
                            conversationId = route.conversationId,
                            assistantName = route.assistantName,
                            conversationRepository = container.conversationRepository,
                            chatRepository = container.chatRepository,
                            turnStates = container.appWsManager.turnStates,
                            wsEvents = container.appWsManager.events,
                            isOnline = { container.isOnline.value },
                        )
                    }
                },
            )
            val state by chatViewModel.state.collectAsStateWithLifecycle()
            // 纠正请求在途时禁止返回：弹栈会取消 viewModelScope，请求结果不确定。
            BackHandler {
                if (chatViewModel.state.value.correcting) return@BackHandler
                if (backStack.lastOrNull() == route) popRoute()
            }
            ChatScreen(
                state = state,
                online = online,
                onInputChange = chatViewModel::onInputChange,
                onSend = chatViewModel::send,
                onStop = chatViewModel::stop,
                onRetry = chatViewModel::retry,
                onReload = chatViewModel::reload,
                onStartCorrection = chatViewModel::startCorrection,
                onCancelCorrection = chatViewModel::cancelCorrection,
                onCorrect = chatViewModel::correct,
                onUseVoiceDraft = chatViewModel::useVoiceDraft,
                onDismissVoiceDraft = chatViewModel::dismissVoiceDraft,
                onDismissSnackbar = chatViewModel::dismissSnackbar,
                onMemories = {
                    pushRoute(
                        AppRoute.Memories(
                            assistantId = route.assistantId,
                            assistantName = route.assistantName,
                        ),
                    )
                },
                onBack = {
                    if (!chatViewModel.state.value.correcting &&
                        backStack.lastOrNull() == route
                    ) {
                        popRoute()
                    }
                },
            )
        }

        is AppRoute.Memories -> {
            val memoriesViewModel: MemoriesViewModel = viewModel(
                viewModelStoreOwner = navigationViewModel.ownerFor(route),
                key = "memories-${route.assistantId}",
                factory = viewModelFactory {
                    initializer {
                        MemoriesViewModel(
                            repository = container.memoryRepository,
                            assistantId = route.assistantId,
                        )
                    }
                },
            )
            val state by memoriesViewModel.state.collectAsStateWithLifecycle()
            // 保存/删除在途时禁止返回，避免取消写请求后状态不确定。
            BackHandler {
                val latest = memoriesViewModel.state.value
                if (latest.saving || latest.deletingId != null) return@BackHandler
                if (backStack.lastOrNull() == route) popRoute()
            }
            MemoriesScreen(
                assistantName = route.assistantName,
                state = state,
                online = online,
                onRefresh = memoriesViewModel::refresh,
                onCreate = memoriesViewModel::startCreate,
                onEdit = memoriesViewModel::startEdit,
                onDelete = memoriesViewModel::delete,
                onEditorChange = memoriesViewModel::updateContent,
                onSave = memoriesViewModel::save,
                onCloseEditor = memoriesViewModel::closeEditor,
                onDismissSnackbar = memoriesViewModel::dismissSnackbar,
                onBack = {
                    val latest = memoriesViewModel.state.value
                    if (!latest.saving && latest.deletingId == null &&
                        backStack.lastOrNull() == route
                    ) {
                        popRoute()
                    }
                },
            )
        }
    }
}

@Composable
private fun LoginRoute(container: AppContainer) {
    val viewModel: LoginViewModel = viewModel(
        factory = viewModelFactory {
            initializer {
                LoginViewModel(
                    repository = container.authRepository,
                    allowCleartext = container.allowCleartext,
                )
            }
        },
    )
    val state by viewModel.state.collectAsStateWithLifecycle()

    LoginScreen(
        state = state,
        onServerUrlChange = viewModel::onServerUrlChange,
        onUsernameChange = viewModel::onUsernameChange,
        onPasswordChange = viewModel::onPasswordChange,
        onSubmit = viewModel::submit,
    )
}
