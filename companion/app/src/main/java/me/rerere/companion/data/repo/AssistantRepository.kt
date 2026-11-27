package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.session.SessionManager

interface AssistantRepository {
    suspend fun list(): AppResult<List<AssistantDto>>
    suspend fun get(id: String): AppResult<AssistantDto>
    suspend fun create(draft: AssistantDraft): AppResult<String>
    suspend fun update(id: String, draft: AssistantDraft): AppResult<Unit>
    suspend fun delete(id: String): AppResult<Unit>
}

class HttpAssistantRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), AssistantRepository {

    override suspend fun list(): AppResult<List<AssistantDto>> = call { it.listAssistants() }

    override suspend fun get(id: String): AppResult<AssistantDto> = call { it.getAssistant(id) }

    override suspend fun create(draft: AssistantDraft): AppResult<String> = call {
        it.createAssistant(draft).id
    }

    override suspend fun update(id: String, draft: AssistantDraft): AppResult<Unit> = call {
        it.updateAssistant(id, draft)
        Unit
    }

    override suspend fun delete(id: String): AppResult<Unit> = call {
        it.deleteAssistant(id)
        Unit
    }
}
