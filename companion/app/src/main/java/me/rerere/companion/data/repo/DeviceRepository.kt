package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.ActionCatalogDto
import me.rerere.companion.data.api.dto.DeviceBindBody
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.api.dto.DeviceRenameBody
import me.rerere.companion.data.api.dto.PairCodeDto
import me.rerere.companion.data.api.dto.PairCodeRequest
import me.rerere.companion.data.session.SessionManager

/** 设备管理 + 动作目录（4C）。写操作失败统一以 [AppResult.Err] 返回，UI 不解析响应体。 */
interface DeviceRepository {
    suspend fun list(): AppResult<List<DeviceDto>>

    suspend fun createPairCode(
        assistantId: String? = null,
        conversationId: String? = null,
    ): AppResult<PairCodeDto>

    suspend fun bind(
        deviceId: String,
        assistantId: String?,
        conversationId: String?,
    ): AppResult<Unit>

    suspend fun rename(deviceId: String, name: String): AppResult<Unit>

    suspend fun revoke(deviceId: String): AppResult<Unit>

    suspend fun catalog(): AppResult<ActionCatalogDto>
}

class HttpDeviceRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), DeviceRepository {

    override suspend fun list(): AppResult<List<DeviceDto>> = call { it.listDevices() }

    override suspend fun createPairCode(
        assistantId: String?,
        conversationId: String?,
    ): AppResult<PairCodeDto> = call {
        it.createPairCode(
            PairCodeRequest(assistantId = assistantId, conversationId = conversationId),
        )
    }

    override suspend fun bind(
        deviceId: String,
        assistantId: String?,
        conversationId: String?,
    ): AppResult<Unit> = call {
        it.bindDevice(
            DeviceBindBody(
                deviceId = deviceId,
                boundAssistantId = assistantId,
                boundConversationId = conversationId,
            ),
        )
        Unit
    }

    override suspend fun rename(deviceId: String, name: String): AppResult<Unit> = call {
        it.renameDevice(deviceId, DeviceRenameBody(name.trim()))
        Unit
    }

    override suspend fun revoke(deviceId: String): AppResult<Unit> = call {
        it.revokeDevice(deviceId)
        Unit
    }

    override suspend fun catalog(): AppResult<ActionCatalogDto> = call { it.actionCatalog() }
}
