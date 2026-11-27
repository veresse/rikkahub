package me.rerere.companion.testutil

import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssistantDraft
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.data.repo.AssistantRepository

class FakeAssistantRepository : AssistantRepository {
    var items: MutableList<AssistantDto> = mutableListOf()
    var listError: AppError? = null
    var detail: AssistantDto? = null
    var detailError: AppError? = null
    var saveError: AppError? = null
    var deleteError: AppError? = null

    val created = mutableListOf<AssistantDraft>()
    val updated = mutableListOf<Pair<String, AssistantDraft>>()
    val deleted = mutableListOf<String>()

    override suspend fun list(): AppResult<List<AssistantDto>> {
        listError?.let { return AppResult.Err(it) }
        return AppResult.Ok(items.toList())
    }

    override suspend fun get(id: String): AppResult<AssistantDto> {
        detailError?.let { return AppResult.Err(it) }
        return detail?.let { AppResult.Ok(it) }
            ?: AppResult.Err(AppError.Http("not_found", "角色不存在"))
    }

    override suspend fun create(draft: AssistantDraft): AppResult<String> {
        saveError?.let { return AppResult.Err(it) }
        created += draft
        return AppResult.Ok("new-id")
    }

    override suspend fun update(id: String, draft: AssistantDraft): AppResult<Unit> {
        saveError?.let { return AppResult.Err(it) }
        updated += id to draft
        return AppResult.Ok(Unit)
    }

    override suspend fun delete(id: String): AppResult<Unit> {
        deleteError?.let { return AppResult.Err(it) }
        deleted += id
        return AppResult.Ok(Unit)
    }
}
