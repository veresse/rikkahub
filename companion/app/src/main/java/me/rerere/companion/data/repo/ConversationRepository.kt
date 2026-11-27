package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.ConversationCreate
import me.rerere.companion.data.api.dto.ConversationDetailDto
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.data.api.dto.MessageTextUpdate
import me.rerere.companion.data.session.SessionManager

interface ConversationRepository {
    suspend fun list(assistantId: String?): AppResult<List<ConversationDto>>
    suspend fun create(assistantId: String, title: String = "新对话"): AppResult<String>
    suspend fun delete(id: String): AppResult<Unit>
    suspend fun detail(id: String): AppResult<ConversationDetailDto>
    suspend fun selectBranch(
        conversationId: String,
        nodeId: String,
        index: Int,
    ): AppResult<Unit>

    suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit>
}

class HttpConversationRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), ConversationRepository {

    override suspend fun list(assistantId: String?): AppResult<List<ConversationDto>> = call {
        it.listConversations(assistantId)
    }

    override suspend fun create(
        assistantId: String,
        title: String,
    ): AppResult<String> = call {
        it.createConversation(ConversationCreate(assistantId = assistantId, title = title)).id
    }

    override suspend fun delete(id: String): AppResult<Unit> = call {
        it.deleteConversation(id)
        Unit
    }

    override suspend fun detail(id: String): AppResult<ConversationDetailDto> = call {
        it.conversationDetail(id)
    }

    override suspend fun selectBranch(
        conversationId: String,
        nodeId: String,
        index: Int,
    ): AppResult<Unit> = call<Unit> {
        it.selectBranch(conversationId, nodeId, index)
    }

    override suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit> = call<Unit> {
        it.correctMessage(conversationId, nodeId, messageId, MessageTextUpdate(text))
    }
}
