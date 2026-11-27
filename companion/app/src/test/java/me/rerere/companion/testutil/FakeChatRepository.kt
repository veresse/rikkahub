package me.rerere.companion.testutil

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.ChatRequestDto
import me.rerere.companion.data.repo.ChatRepository
import me.rerere.companion.data.sse.ChatEvent

class FakeChatRepository : ChatRepository {
    /** 默认一轮立即完成；需要流式/挂起时在测试里替换。 */
    var streamFactory: (ChatRequestDto) -> Flow<ChatEvent> = {
        flowOf(ChatEvent.Done("stop", "m1", "u1", replayed = false))
    }
    var correctError: AppError? = null

    val requests = mutableListOf<ChatRequestDto>()
    val corrections = mutableListOf<FakeConversationRepository.Correction>()

    override fun stream(request: ChatRequestDto): Flow<ChatEvent> {
        requests += request
        return streamFactory(request)
    }

    override suspend fun correctMessage(
        conversationId: String,
        nodeId: String,
        messageId: String,
        text: String,
    ): AppResult<Unit> {
        correctError?.let { return AppResult.Err(it) }
        corrections += FakeConversationRepository.Correction(
            conversationId = conversationId,
            nodeId = nodeId,
            messageId = messageId,
            text = text,
        )
        return AppResult.Ok(Unit)
    }
}
