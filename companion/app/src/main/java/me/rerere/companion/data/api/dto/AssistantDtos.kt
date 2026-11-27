package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class AssistantDto(
    val id: String,
    val name: String = "",
    @SerialName("system_prompt") val systemPrompt: String = "",
    val temperature: Double? = null,
    @SerialName("top_p") val topP: Double? = null,
    @SerialName("context_message_limit") val contextMessageLimit: Int = 0,
    @SerialName("enable_memory") val enableMemory: Boolean = false,
    @SerialName("use_global_memory") val useGlobalMemory: Boolean = false,
    @SerialName("enable_web_search") val enableWebSearch: Boolean = false,
    @SerialName("web_search_mode") val webSearchMode: String = "off",
    @SerialName("enable_action_sync") val enableActionSync: Boolean = true,
    @SerialName("model_id") val modelId: String? = null,
    val avatar: JsonObject = JsonObject(emptyMap()),
)

/**
 * 角色写入请求体，同时作为 UI 草稿。
 *
 * `web_search_mode` 是权威三态；`enable_web_search` 由它在服务端派生，
 * App 只发送三态以保持单一事实来源。
 */
@Serializable
data class AssistantDraft(
    val name: String = "",
    @SerialName("system_prompt") val systemPrompt: String = "",
    val temperature: Double = 0.7,
    @SerialName("top_p") val topP: Double = 0.9,
    @SerialName("context_message_limit") val contextMessageLimit: Int = 0,
    @SerialName("enable_memory") val enableMemory: Boolean = false,
    @SerialName("use_global_memory") val useGlobalMemory: Boolean = false,
    @SerialName("web_search_mode") val webSearchMode: String = "off",
    @SerialName("enable_action_sync") val enableActionSync: Boolean = true,
    @SerialName("model_id") val modelId: String? = null,
    val avatar: JsonObject = JsonObject(emptyMap()),
)
