package me.rerere.companion.ui.navigation

import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.runtime.toMutableStateList
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * 手写导航栈的保存/恢复。
 *
 * 只保存路由本身，不保存页面状态；进程被回收后重新进入聊天页时会重新拉详情。
 * 配置变更（旋转）由 [NavigationViewModel] 保住 ViewModel，不会打断正在生成的流。
 */
internal val AppRouteBackStackSaver =
    listSaver<SnapshotStateList<AppRoute>, String>(
        save = { stack -> stack.map(::encodeRoute) },
        restore = { encoded -> encoded.map(::decodeRoute).toMutableStateList() },
    )

@Serializable
private data class RouteRecord(
    val type: String,
    val a: String? = null,
    val b: String? = null,
    val c: String? = null,
)

private val json = Json { ignoreUnknownKeys = true }

private fun encodeRoute(route: AppRoute): String = json.encodeToString(
    RouteRecord(
        type = when (route) {
            AppRoute.Assistants -> "assistants"
            is AppRoute.Conversations -> "conversations"
            is AppRoute.Chat -> "chat"
            is AppRoute.Memories -> "memories"
            is AppRoute.AssistantEdit -> "assistant-edit"
        },
        a = when (route) {
            AppRoute.Assistants -> null
            is AppRoute.Conversations -> route.assistantId
            is AppRoute.Chat -> route.conversationId
            is AppRoute.Memories -> route.assistantId
            is AppRoute.AssistantEdit -> route.assistantId
        },
        b = when (route) {
            AppRoute.Assistants -> null
            is AppRoute.Conversations -> route.assistantName
            is AppRoute.Chat -> route.assistantId
            is AppRoute.Memories -> route.assistantName
            is AppRoute.AssistantEdit -> route.instanceId
        },
        c = when (route) {
            is AppRoute.Chat -> route.assistantName
            else -> null
        },
    ),
)

private fun decodeRoute(encoded: String): AppRoute {
    val record = runCatching { json.decodeFromString<RouteRecord>(encoded) }.getOrNull()
        ?: return AppRoute.Assistants
    return when (record.type) {
        "conversations" -> AppRoute.Conversations(
            assistantId = record.a.orEmpty(),
            assistantName = record.b.orEmpty(),
        )

        "chat" -> AppRoute.Chat(
            conversationId = record.a.orEmpty(),
            assistantId = record.b.orEmpty(),
            assistantName = record.c.orEmpty(),
        )

        "memories" -> AppRoute.Memories(
            assistantId = record.a.orEmpty(),
            assistantName = record.b.orEmpty(),
        )

        "assistant-edit" -> AppRoute.AssistantEdit(
            assistantId = record.a,
            instanceId = record.b ?: java.util.UUID.randomUUID().toString(),
        )

        else -> AppRoute.Assistants
    }
}

/** 仅用于测试：验证损坏/未知的保存数据会安全回到角色列表。 */
internal fun decodeRouteForTest(encoded: String): AppRoute = decodeRoute(encoded)

/** 仅用于测试：暴露编码后的形态。 */
internal fun encodeRouteForTest(route: AppRoute): String = encodeRoute(route)
