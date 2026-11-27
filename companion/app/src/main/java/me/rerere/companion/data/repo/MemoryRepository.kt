package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.MemoryDto
import me.rerere.companion.data.api.dto.MemoryUpsert
import me.rerere.companion.data.session.SessionManager

interface MemoryRepository {
    suspend fun list(assistantId: String?): AppResult<List<MemoryDto>>
    suspend fun create(assistantId: String?, content: String): AppResult<String>
    suspend fun update(id: String, content: String): AppResult<Unit>
    suspend fun delete(id: String): AppResult<Unit>
}

class HttpMemoryRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), MemoryRepository {

    override suspend fun list(assistantId: String?): AppResult<List<MemoryDto>> = call {
        it.listMemories(assistantId)
    }

    override suspend fun create(assistantId: String?, content: String): AppResult<String> = call {
        it.createMemory(MemoryUpsert(assistantId = assistantId, content = content)).id
    }

    override suspend fun update(id: String, content: String): AppResult<Unit> = call<Unit> {
        it.updateMemory(id, MemoryUpsert(content = content))
    }

    override suspend fun delete(id: String): AppResult<Unit> = call<Unit> {
        it.deleteMemory(id)
    }
}
