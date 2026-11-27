package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `GET /api/devices` 的一行。
 *
 * `online` 由服务端按 `/device/ws` 链路实时计算（4C 新增）；`state`/`volume` 等
 * 是最后一次上报后的落库快照，可能比 `online` 旧。
 */
@Serializable
data class DeviceDto(
    val id: String,
    val name: String = "",
    @SerialName("protocol_version") val protocolVersion: Int = 2,
    @SerialName("firmware_version") val firmwareVersion: String = "",
    val capabilities: List<String> = emptyList(),
    @SerialName("bound_assistant_id") val boundAssistantId: String? = null,
    @SerialName("bound_conversation_id") val boundConversationId: String? = null,
    @SerialName("is_active") val isActive: Boolean = true,
    val online: Boolean = false,
    val volume: Int = 70,
    @SerialName("idle_enabled") val idleEnabled: Boolean = true,
    @SerialName("pan_offset") val panOffset: Double = 0.0,
    @SerialName("tilt_offset") val tiltOffset: Double = 0.0,
    val state: String = "",
    @SerialName("last_seen_at") val lastSeenAt: Long? = null,
)

@Serializable
data class PairCodeDto(
    val code: String,
    @SerialName("expires_in") val expiresIn: Int,
)

@Serializable
data class PairCodeRequest(
    @SerialName("assistant_id") val assistantId: String? = null,
    @SerialName("conversation_id") val conversationId: String? = null,
)

@Serializable
data class DeviceBindBody(
    @SerialName("device_id") val deviceId: String,
    @SerialName("bound_assistant_id") val boundAssistantId: String? = null,
    @SerialName("bound_conversation_id") val boundConversationId: String? = null,
)

@Serializable
data class DeviceRenameBody(
    val name: String,
)
