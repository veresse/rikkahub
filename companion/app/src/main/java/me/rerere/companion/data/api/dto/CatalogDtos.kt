package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `GET /api/actions/catalog` 的响应（`companion/shared/actions.json` 原样）。
 *
 * App 只取渲染遥控面板需要的字段；`llm_description_zh`、`keyframes` 等未知字段
 * 由 `ignoreUnknownKeys` 忽略。
 */
@Serializable
data class ActionCatalogDto(
    val version: String = "",
    val categories: Map<String, String> = emptyMap(),
    val limits: CatalogLimitsDto = CatalogLimitsDto(),
    val actions: List<CatalogActionDto> = emptyList(),
)

@Serializable
data class CatalogLimitsDto(
    val pan: AxisLimitDto = AxisLimitDto(),
    val tilt: AxisLimitDto = AxisLimitDto(min = -20.0, max = 45.0),
    val volume: VolumeLimitDto = VolumeLimitDto(),
)

@Serializable
data class AxisLimitDto(
    val min: Double = -90.0,
    val max: Double = 90.0,
)

@Serializable
data class VolumeLimitDto(
    val min: Int = 0,
    val max: Int = 100,
    val default: Int = 70,
    /** 静音档位；协议约定 0 为静音但不影响拾音。 */
    val muted: Int = 0,
)

@Serializable
data class CatalogActionDto(
    val name: String,
    @SerialName("label_zh") val labelZh: String = "",
    val category: String = "",
    val supports: ActionSupportsDto = ActionSupportsDto(),
    @SerialName("max_duration_ms") val maxDurationMs: Int = 3000,
)

@Serializable
data class ActionSupportsDto(
    val intensity: Boolean = false,
    val speed: Boolean = false,
    val repeat: Boolean = false,
)
