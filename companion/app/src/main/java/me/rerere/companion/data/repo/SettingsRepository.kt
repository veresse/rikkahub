package me.rerere.companion.data.repo

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.dto.SecretsUpdateBody
import me.rerere.companion.data.api.dto.SettingsDto
import me.rerere.companion.data.api.dto.SettingsUpdateBody
import me.rerere.companion.data.session.SessionManager

/**
 * 4D：服务端设置。
 *
 * 所有方法的返回值都是脱敏视图；Key 只通过 [updateSecrets] 单向发送，
 * 仓库不缓存、不记录入参。
 */
interface SettingsRepository {
    suspend fun load(): AppResult<SettingsDto>

    suspend fun update(body: SettingsUpdateBody): AppResult<SettingsDto>

    suspend fun updateSecrets(body: SecretsUpdateBody): AppResult<SettingsDto>

    suspend fun clearSecret(key: String): AppResult<SettingsDto>
}

class HttpSettingsRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), SettingsRepository {

    override suspend fun load(): AppResult<SettingsDto> = call { it.getSettings() }

    override suspend fun update(body: SettingsUpdateBody): AppResult<SettingsDto> =
        call { it.updateSettings(body) }

    override suspend fun updateSecrets(body: SecretsUpdateBody): AppResult<SettingsDto> =
        call { it.updateSecrets(body) }

    override suspend fun clearSecret(key: String): AppResult<SettingsDto> =
        call { it.clearSecret(key) }
}
