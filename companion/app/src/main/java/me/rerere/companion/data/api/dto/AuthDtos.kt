package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    @SerialName("device_name") val deviceName: String = "android",
)

@Serializable
data class LoginResponse(
    val token: String,
    @SerialName("token_id") val tokenId: String,
    val username: String,
    @SerialName("expires_at") val expiresAt: Long,
)
