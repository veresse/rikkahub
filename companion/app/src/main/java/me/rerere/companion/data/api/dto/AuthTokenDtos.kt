package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** `GET /api/auth/tokens` 的一行（4A 已存在的端点，4D 才在 App 里消费）。 */
@Serializable
data class AuthTokenDto(
    val id: String,
    val username: String = "",
    @SerialName("device_name") val deviceName: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("last_used_at") val lastUsedAt: Long? = null,
    @SerialName("expires_at") val expiresAt: Long = 0,
    @SerialName("revoked_at") val revokedAt: Long? = null,
)
