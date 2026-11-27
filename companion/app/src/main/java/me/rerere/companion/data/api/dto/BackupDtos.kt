package me.rerere.companion.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** `POST /api/backup/preview` 的预览统计；不含备份正文。 */
@Serializable
data class BackupSummaryDto(
    @SerialName("assistant_count") val assistantCount: Int = 0,
    @SerialName("conversation_count") val conversationCount: Int = 0,
    @SerialName("message_node_count") val messageNodeCount: Int = 0,
    @SerialName("total_message_versions") val totalMessageVersions: Int = 0,
    @SerialName("memory_count") val memoryCount: Int = 0,
    @SerialName("unsupported_features") val unsupportedFeatures: List<String> = emptyList(),
)

@Serializable
data class BackupPreviewDto(
    @SerialName("preview_id") val previewId: String,
    val summary: BackupSummaryDto = BackupSummaryDto(),
    @SerialName("assistants_sample") val assistantsSample: List<String> = emptyList(),
    @SerialName("conversations_sample") val conversationsSample: List<String> = emptyList(),
)

/** 确认导入只提交预览 id；服务端按 Token 校验归属与有效期。 */
@Serializable
data class BackupConfirmBody(
    @SerialName("preview_id") val previewId: String,
)

@Serializable
data class BackupResultDto(
    val status: String = "",
    @SerialName("imported_assistants") val importedAssistants: Int = 0,
    @SerialName("imported_conversations") val importedConversations: Int = 0,
    @SerialName("imported_nodes") val importedNodes: Int = 0,
    @SerialName("imported_memories") val importedMemories: Int = 0,
)
