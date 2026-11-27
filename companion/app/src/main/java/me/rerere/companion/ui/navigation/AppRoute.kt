package me.rerere.companion.ui.navigation

/**
 * 手写导航栈的路由表。
 *
 * 4B 只用到 [Assistants] 与 [AssistantEdit]；[Conversations]/[Chat]/[Memories]
 * 由 Task 9 接入（同一份路由定义，避免后续改签名）。
 */
sealed interface AppRoute {
    data object Assistants : AppRoute
    data class Conversations(val assistantId: String, val assistantName: String) : AppRoute
    data class Chat(
        val conversationId: String,
        val assistantId: String,
        val assistantName: String,
    ) : AppRoute

    data class Memories(val assistantId: String, val assistantName: String) : AppRoute
    data class AssistantEdit(
        val assistantId: String?,
        /** 每次导航都换新实例，避免“新建”复用上次填过的 ViewModel。 */
        val instanceId: String = java.util.UUID.randomUUID().toString(),
    ) : AppRoute
}
