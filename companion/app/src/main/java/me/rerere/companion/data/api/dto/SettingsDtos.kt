package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `GET /api/settings` 的脱敏视图。
 *
 * 服务端**不会**返回任何 Key 明文、掩码或片段；[DeepseekSettingsDto.configured]
 * 与 [DeepseekSettingsDto.source] 只表达“是否已配置 / 来源”。
 */
@Serializable
data class SettingsDto(
    val deepseek: DeepseekSettingsDto = DeepseekSettingsDto(),
    val dashscope: DashscopeSettingsDto = DashscopeSettingsDto(),
    val voice: VoiceSettingsDto = VoiceSettingsDto(),
    val search: SearchSettingsDto = SearchSettingsDto(),
)

@Serializable
data class DeepseekSettingsDto(
    val configured: Boolean = false,
    val source: String = "unset",
    @SerialName("base_url") val baseUrl: String = "",
    val model: String = "",
    /** `decrypt_failed` 表示 DB 密文无法解密，按未配置处理。 */
    val error: String? = null,
)

@Serializable
data class DashscopeSettingsDto(
    val configured: Boolean = false,
    val source: String = "unset",
    val region: String = "",
    @SerialName("asr_model") val asrModel: String = "",
    @SerialName("tts_model") val ttsModel: String = "",
    @SerialName("tts_voice") val ttsVoice: String = "",
    val error: String? = null,
)

@Serializable
data class VoiceSettingsDto(
    val enabled: Boolean = true,
    val provider: String = "unavailable",
    /** 实际生效的语音提供方：`dashscope` / `mock` / `unavailable`。 */
    val effective: String = "unavailable",
    /** `dashscope_key_missing`、`voice_disabled` 等；正常时为 null。 */
    val reason: String? = null,
)

@Serializable
data class SearchSettingsDto(
    val enabled: Boolean = true,
    val provider: String = "builtin_bing",
    @SerialName("requires_key") val requiresKey: Boolean = false,
)

@Serializable
data class SettingsUpdateBody(
    @SerialName("deepseek_base_url") val deepseekBaseUrl: String? = null,
    @SerialName("deepseek_model") val deepseekModel: String? = null,
    @SerialName("voice_enabled") val voiceEnabled: Boolean? = null,
    @SerialName("voice_provider") val voiceProvider: String? = null,
    @SerialName("dashscope_region") val dashscopeRegion: String? = null,
    @SerialName("dashscope_asr_model") val dashscopeAsrModel: String? = null,
    @SerialName("dashscope_tts_model") val dashscopeTtsModel: String? = null,
    @SerialName("dashscope_tts_voice") val dashscopeTtsVoice: String? = null,
    @SerialName("search_enabled") val searchEnabled: Boolean? = null,
)

/** 只写不读：服务端响应仍是脱敏视图。 */
@Serializable
data class SecretsUpdateBody(
    @SerialName("deepseek_api_key") val deepseekApiKey: String? = null,
    @SerialName("dashscope_api_key") val dashscopeApiKey: String? = null,
)
