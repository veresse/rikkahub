package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class MemoryDto(
    val id: String,
    @SerialName("assistant_id") val assistantId: String? = null,
    val path: String = "default.md",
    val content: String = "",
    @SerialName("updated_at") val updatedAt: Long = 0,
)

@Serializable
data class MemoryUpsert(
    @SerialName("assistant_id") val assistantId: String? = null,
    val content: String,
    val path: String = "default.md",
)
