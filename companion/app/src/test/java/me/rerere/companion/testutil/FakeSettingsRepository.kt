package me.rerere.companion.testutil

import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.SecretsUpdateBody
import me.rerere.companion.data.api.dto.SettingsDto
import me.rerere.companion.data.api.dto.SettingsUpdateBody
import me.rerere.companion.data.repo.SettingsRepository

class FakeSettingsRepository : SettingsRepository {
    var view: SettingsDto = SettingsDto()

    var loadResult: AppResult<SettingsDto>? = null
    var updateResult: AppResult<SettingsDto>? = null
    var secretsResult: AppResult<SettingsDto>? = null
    var clearResult: AppResult<SettingsDto>? = null

    var loadCalls: Int = 0
    var updateCalls: Int = 0
    var secretsCalls: Int = 0
    var clearCalls: Int = 0

    var lastUpdateBody: SettingsUpdateBody? = null
        private set
    var lastSecretsBody: SecretsUpdateBody? = null
        private set
    var lastClearedKey: String? = null
        private set

    override suspend fun load(): AppResult<SettingsDto> {
        loadCalls += 1
        return loadResult ?: AppResult.Ok(view)
    }

    override suspend fun update(body: SettingsUpdateBody): AppResult<SettingsDto> {
        updateCalls += 1
        lastUpdateBody = body
        return updateResult ?: AppResult.Ok(view)
    }

    override suspend fun updateSecrets(body: SecretsUpdateBody): AppResult<SettingsDto> {
        secretsCalls += 1
        lastSecretsBody = body
        return secretsResult ?: AppResult.Ok(view)
    }

    override suspend fun clearSecret(key: String): AppResult<SettingsDto> {
        clearCalls += 1
        lastClearedKey = key
        return clearResult ?: AppResult.Ok(view)
    }
}
