package me.rerere.companion.ui.navigation

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner

/**
 * 手写导航栈的 ViewModel 宿主。
 *
 * `viewModel()` 默认挂在 Activity 的 `ViewModelStore` 上，手写栈弹栈时不会清除，
 * 会导致离开聊天页后 SSE 仍继续读流、再次进入时复用旧消息。这里按路由保存
 * 独立的 `ViewModelStore`：路由留在栈里就保留状态，弹栈/登出时显式 `clear()`，
 * 从而触发 `onCleared()` 取消流与收集器。
 *
 * 自身挂在 Activity 上，旋转时不会丢失这些 store，正在生成的聊天不会被旋转打断。
 */
class NavigationViewModel : ViewModel() {

    private val owners = mutableMapOf<String, RouteViewModelStoreOwner>()

    fun ownerFor(route: AppRoute): ViewModelStoreOwner =
        owners.getOrPut(route.viewModelKey()) { RouteViewModelStoreOwner() }

    fun clearRoute(route: AppRoute) {
        owners.remove(route.viewModelKey())?.viewModelStore?.clear()
    }

    fun clearAll() {
        owners.values.forEach { it.viewModelStore.clear() }
        owners.clear()
    }

    override fun onCleared() {
        clearAll()
        super.onCleared()
    }
}

private class RouteViewModelStoreOwner : ViewModelStoreOwner {
    override val viewModelStore: ViewModelStore = ViewModelStore()
}

/**
 * 路由的稳定标识。
 *
 * `AssistantEdit` 的 `instanceId` 保证“新建角色”每次都是新实例；其余路由按
 * 业务主键区分。调用方应避免把完全相同的路由重复压栈（见 `pushRoute`）。
 */
fun AppRoute.viewModelKey(): String = when (this) {
    AppRoute.Assistants -> "assistants"
    AppRoute.Devices -> "devices"
    is AppRoute.Conversations -> "conversations:$assistantId"
    is AppRoute.Chat -> "chat:$conversationId"
    is AppRoute.Memories -> "memories:$assistantId"
    is AppRoute.Remote -> "remote:$deviceId"
    is AppRoute.AssistantEdit -> "assistant-edit:$instanceId"
}
