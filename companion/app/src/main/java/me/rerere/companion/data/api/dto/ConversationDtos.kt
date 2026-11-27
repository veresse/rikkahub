package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ConversationDto(
    val id: String,
    @SerialName("assistant_id") val assistantId: String = "",
    val title: String = "",
    @SerialName("is_pinned") val isPinned: Boolean = false,
    @SerialName("custom_system_prompt") val customSystemPrompt: String? = null,
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
)

@Serializable
data class ConversationDetailDto(
    val id: String,
    @SerialName("assistant_id") val assistantId: String = "",
    val title: String = "",
    @SerialName("is_pinned") val isPinned: Boolean = false,
    @SerialName("custom_system_prompt") val customSystemPrompt: String? = null,
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
    val nodes: List<MessageNodeDto> = emptyList(),
)

@Serializable
data class MessageNodeDto(
    val id: String,
    @SerialName("node_index") val nodeIndex: Int = 0,
    val messages: List<UiMessageDto> = emptyList(),
    @SerialName("select_index") val selectIndex: Int = 0,
    @SerialName("total_branches") val totalBranches: Int = 1,
)

@Serializable
data class UiMessageDto(
    val id: String = "",
    val role: String = "USER",
    val parts: List<MessagePartDto> = emptyList(),
)

@Serializable
data class MessagePartDto(
    val text: String? = null,
    val reasoning: String? = null,
)

@Serializable
data class ConversationCreate(
    @SerialName("assistant_id") val assistantId: String,
    val title: String = "新对话",
)

@Serializable
data class MessageTextUpdate(
    val text: String,
)
