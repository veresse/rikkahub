package me.rerere.companion.testutil

import kotlinx.coroutines.CompletableDeferred
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.ConversationDetailDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.repo.ConversationRepository

class FakeConversationRepository : ConversationRepository {
    var items: MutableList<ConversationDto> = mutableListOf()
    var listError: AppError? = null
    var detail: ConversationDetailDto? = null
    var detailError: AppError? = null
    var createError: AppError? = null
    var deleteError: AppError? = null
    var correctError: AppError? = null

    /** 非空时 `list()` 会挂起，直到测试完成它；用于模拟慢请求与竞态。 */
    var listGate: CompletableDeferred<Unit>? = null

    val created = mutableListOf<String>()
    val deleted = mutableListOf<String>()
    val corrections = mutableListOf<Correction>()
    val detailRequests = mutableListOf<String>()

    data class Correction(
        val conversationId: String,
        val nodeId: String,
        val messageId: String,
        val text: String,
    )

    override suspend fun list(assistantId: String?): AppResult<List<ConversationDto>> {
        listGate?.await()
        listError?.let { return AppResult.Err(it) }
        return AppResult.Ok(items.toList())
    }

    override suspend fun create(assistantId: String, title: String): AppResult<String> {
        createError?.let { return AppResult.Err(it) }
        created += assistantId
        val id = "c-${created.size}"
        items = (items + ConversationDto(id = id, assistantId = assistantId, title = title))
            .toMutableList()
        return AppResult.Ok(id)
    }

    override suspend fun delete(id: String): AppResult<Unit> {
        deleteError?.let { return AppResult.Err(it) }
        deleted += id
        items = items.filterNot { it.id == id }.toMutableList()
        return AppResult.Ok(Unit)
    }

    override suspend fun detail(id: String): AppResult<ConversationDetailDto> {
        detailRequests += id
        detailError?.let { return AppResult.Err(it) }
        val value = detail ?: ConversationDetailDto(id = id)
        return AppResult.Ok(value)
    }

    override suspend fun selectBranch(
        conversationId: String,
        nodeId: String,
        index: Int,
    ): AppResult<Unit> = AppResult.Ok(Unit)

    override suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit> {
        correctError?.let { return AppResult.Err(it) }
        corrections += Correction(conversationId, nodeId, messageId, text)
        return AppResult.Ok(Unit)
    }
}
