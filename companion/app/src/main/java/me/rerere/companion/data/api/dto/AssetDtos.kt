package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** 已启用/上一版素材包；不含服务器存储路径。 */
@Serializable
data class AssetPackageDto(
    val id: String,
    val name: String = "",
    val version: String = "",
    val fps: Int = 12,
    @SerialName("frame_count") val frameCount: Int = 0,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val sha256: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("activated_at") val activatedAt: Long = 0,
)

@Serializable
data class AssetFallbackDto(
    val name: String = "占位图",
    val version: String = "builtin",
    @SerialName("frame_count") val frameCount: Int = 1,
)

@Serializable
data class AssetViewDto(
    val active: AssetPackageDto? = null,
    val previous: AssetPackageDto? = null,
    val fallback: AssetFallbackDto = AssetFallbackDto(),
)

/** `POST /api/assets/preview` 的校验结果（已暂存，未生效）。 */
@Serializable
data class AssetUploadDto(
    @SerialName("upload_id") val uploadId: String,
    val name: String = "",
    val version: String = "",
    val fps: Int = 12,
    @SerialName("frame_count") val frameCount: Int = 0,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val sha256: String = "",
)

@Serializable
data class AssetConfirmBody(
    @SerialName("upload_id") val uploadId: String,
)
