package me.rerere.companion.testutil

import kotlinx.coroutines.CompletableDeferred
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.ActionCatalogDto
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.api.dto.PairCodeDto
import me.rerere.companion.data.repo.DeviceRepository

class FakeDeviceRepository : DeviceRepository {
    var devices: MutableList<DeviceDto> = mutableListOf()
    var listError: AppError? = null
    var pairCode: PairCodeDto? = null
    var pairError: AppError? = null
    var catalog: ActionCatalogDto = ActionCatalogDto()
    var catalogError: AppError? = null
    var bindError: AppError? = null
    var renameError: AppError? = null
    var revokeError: AppError? = null

    /** 非空时对应请求会挂起，直到测试完成它；用于模拟慢请求与竞态。 */
    var listGate: CompletableDeferred<Unit>? = null
    var pairGate: CompletableDeferred<Unit>? = null
    var renameGate: CompletableDeferred<Unit>? = null
    var revokeGate: CompletableDeferred<Unit>? = null

    val pairRequests = mutableListOf<Pair<String?, String?>>()
    val binds = mutableListOf<Triple<String, String?, String?>>()
    val renames = mutableListOf<Pair<String, String>>()
    val revoked = mutableListOf<String>()
    var listCalls = 0
    var catalogCalls = 0

    override suspend fun list(): AppResult<List<DeviceDto>> {
        listCalls += 1
        // 先快照再等待：gate 模拟“请求在途”，返回的必须是调用当时的列表。
        val snapshot = devices.toList()
        listGate?.await()
        listError?.let { return AppResult.Err(it) }
        return AppResult.Ok(snapshot)
    }

    override suspend fun createPairCode(
        assistantId: String?,
        conversationId: String?,
    ): AppResult<PairCodeDto> {
        pairRequests += assistantId to conversationId
        pairGate?.await()
        pairError?.let { return AppResult.Err(it) }
        return AppResult.Ok(pairCode ?: PairCodeDto(code = "123456", expiresIn = 120))
    }

    override suspend fun bind(
        deviceId: String,
        assistantId: String?,
        conversationId: String?,
    ): AppResult<Unit> {
        binds += Triple(deviceId, assistantId, conversationId)
        bindError?.let { return AppResult.Err(it) }
        return AppResult.Ok(Unit)
    }

    override suspend fun rename(deviceId: String, name: String): AppResult<Unit> {
        renames += deviceId to name
        renameGate?.await()
        renameError?.let { return AppResult.Err(it) }
        return AppResult.Ok(Unit)
    }

    override suspend fun revoke(deviceId: String): AppResult<Unit> {
        revoked += deviceId
        revokeGate?.await()
        revokeError?.let { return AppResult.Err(it) }
        return AppResult.Ok(Unit)
    }

    override suspend fun catalog(): AppResult<ActionCatalogDto> {
        catalogCalls += 1
        catalogError?.let { return AppResult.Err(it) }
        return AppResult.Ok(catalog)
    }
}
