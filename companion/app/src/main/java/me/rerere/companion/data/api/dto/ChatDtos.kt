package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ChatRequestDto(
    @SerialName("conversation_id") val conversationId: String,
    val message: String,
    @SerialName("client_msg_id") val clientMsgId: String? = null,
    @SerialName("regenerate_node_id") val regenerateNodeId: String? = null,
)

@Serializable
data class CreatedDto(
    val id: String,
    val status: String = "created",
)

@Serializable
data class StatusDto(
    val status: String = "ok",
)

@Serializable
data class CorrectionDto(
    val status: String = "updated",
    @SerialName("node_id") val nodeId: String = "",
    @SerialName("message_id") val messageId: String = "",
    val text: String = "",
)
