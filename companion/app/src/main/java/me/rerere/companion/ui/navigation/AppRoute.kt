package me.rerere.companion.ui.navigation

/**
 * 手写导航栈的路由表。
 *
 * 4B 用 [Assistants]/[Conversations]/[Chat]/[Memories]/[AssistantEdit]；
 * 4C 追加 [Devices] 与 [Remote]。
 */
sealed interface AppRoute {
    data object Assistants : AppRoute
    data object Devices : AppRoute
    data class Conversations(val assistantId: String, val assistantName: String) : AppRoute
    data class Chat(
        val conversationId: String,
        val assistantId: String,
        val assistantName: String,
    ) : AppRoute

    data class Memories(val assistantId: String, val assistantName: String) : AppRoute
    data class Remote(val deviceId: String, val deviceName: String) : AppRoute
    data class AssistantEdit(
        val assistantId: String?,
        /** 每次导航都换新实例，避免“新建”复用上次填过的 ViewModel。 */
        val instanceId: String = java.util.UUID.randomUUID().toString(),
    ) : AppRoute
}
