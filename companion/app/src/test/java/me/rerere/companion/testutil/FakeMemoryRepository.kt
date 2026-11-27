package me.rerere.companion.testutil

import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.MemoryDto
import me.rerere.companion.data.repo.MemoryRepository

class FakeMemoryRepository : MemoryRepository {
    var items: MutableList<MemoryDto> = mutableListOf()
    var listError: AppError? = null
    var saveError: AppError? = null
    var deleteError: AppError? = null

    val created = mutableListOf<Pair<String?, String>>()
    val updated = mutableListOf<Pair<String, String>>()
    val deleted = mutableListOf<String>()

    override suspend fun list(assistantId: String?): AppResult<List<MemoryDto>> {
        listError?.let { return AppResult.Err(it) }
        // 与服务端一致：列某个角色时同时返回全局记忆。
        return AppResult.Ok(
            items.filter {
                assistantId == null || it.assistantId == null || it.assistantId == assistantId
            },
        )
    }

    override suspend fun create(assistantId: String?, content: String): AppResult<String> {
        saveError?.let { return AppResult.Err(it) }
        created += assistantId to content
        val id = "m-${created.size}"
        items = (items + MemoryDto(id = id, assistantId = assistantId, content = content))
            .toMutableList()
        return AppResult.Ok(id)
    }

    override suspend fun update(id: String, content: String): AppResult<Unit> {
        saveError?.let { return AppResult.Err(it) }
        updated += id to content
        items = items.map { if (it.id == id) it.copy(content = content) else it }.toMutableList()
        return AppResult.Ok(Unit)
    }

    override suspend fun delete(id: String): AppResult<Unit> {
        deleteError?.let { return AppResult.Err(it) }
        deleted += id
        items = items.filterNot { it.id == id }.toMutableList()
        return AppResult.Ok(Unit)
    }
}
