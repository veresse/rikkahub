package me.rerere.companion.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AuthTokenDto
import me.rerere.companion.data.api.dto.SecretsUpdateBody
import me.rerere.companion.data.api.dto.SettingsDto
import me.rerere.companion.data.api.dto.SettingsUpdateBody
import me.rerere.companion.data.repo.SettingsRepository
import me.rerere.companion.data.repo.TokenRepository
import me.rerere.companion.data.session.SessionManager

/**
 * 设置页表单。Key 输入只存在 [deepseekKeyDraft] / [dashscopeKeyDraft] 两个内存字段：
 * 保存成功立即清空，绝不复用 `rememberSaveable`，也不写入任何日志/错误文案。
 */
data class SettingsUiState(
    val loading: Boolean = false,
    val saving: Boolean = false,
    val error: String? = null,
    val deepseekBaseUrl: String = "",
    val deepseekModel: String = "",
    val deepseekConfigured: Boolean = false,
    val deepseekSource: String = "unset",
    val deepseekKeyDraft: String = "",
    val dashscopeConfigured: Boolean = false,
    val dashscopeSource: String = "unset",
    val dashscopeKeyDraft: String = "",
    val dashscopeRegion: String = "",
    val dashscopeAsrModel: String = "",
    val dashscopeTtsModel: String = "",
    val dashscopeTtsVoice: String = "",
    val voiceEnabled: Boolean = true,
    val voiceProvider: String = "unavailable",
    val voiceEffective: String = "unavailable",
    val voiceReason: String? = null,
    val searchEnabled: Boolean = true,
    val tokens: List<AuthTokenDto> = emptyList(),
    val tokensLoading: Boolean = false,
    val tokensError: String? = null,
    val revokingId: String? = null,
)

sealed interface SettingsEvent {
    data class Message(val text: String) : SettingsEvent

    /** 撤销了当前 Token：本地会话已清空，UI 应回登录页。 */
    data object LoggedOut : SettingsEvent
}

class SettingsViewModel(
    private val settingsRepository: SettingsRepository,
    private val tokenRepository: TokenRepository,
    private val sessionManager: SessionManager,
    private val isOnline: () -> Boolean,
) : ViewModel() {

    private val _state = MutableStateFlow(SettingsUiState())
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<SettingsEvent>(
        extraBufferCapacity = 8,
    )
    val events: SharedFlow<SettingsEvent> = _events.asSharedFlow()

    init {
        load()
    }

    fun load() {
        if (_state.value.loading) return
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            // 设置与 Token 并行：Token 列表失败不能挡住设置表单可用。
            val settingsJob = async { settingsRepository.load() }
            val tokensJob = async { tokenRepository.list() }

            when (val result = settingsJob.await()) {
                is AppResult.Ok -> _state.update {
                    it.copy(loading = false, error = null).applyView(result.value)
                }

                is AppResult.Err -> _state.update {
                    it.copy(loading = false, error = result.error.message)
                }
            }

            when (val result = tokensJob.await()) {
                is AppResult.Ok -> _state.update {
                    it.copy(
                        tokens = visibleTokens(result.value),
                        tokensLoading = false,
                        tokensError = null,
                    )
                }

                is AppResult.Err -> _state.update {
                    it.copy(tokensLoading = false, tokensError = result.error.message)
                }
            }
        }
    }

    // ---- DeepSeek ----

    fun setDeepseekBaseUrl(value: String) {
        _state.update { it.copy(deepseekBaseUrl = value) }
    }

    fun setDeepseekModel(value: String) {
        _state.update { it.copy(deepseekModel = value) }
    }

    fun setDeepseekKeyDraft(value: String) {
        _state.update { it.copy(deepseekKeyDraft = value) }
    }

    /** 普通字段 + （可选）Key 一起保存；Key 只在请求体里出现。 */
    fun saveDeepseek() {
        if (!requireWritable()) return
        val current = _state.value
        val baseUrl = current.deepseekBaseUrl.trim()
        if (baseUrl.isEmpty()) {
            emit("服务器地址不能为空")
            return
        }
        val model = current.deepseekModel.trim().takeIf { it.isNotEmpty() }
        val keyDraft = current.deepseekKeyDraft.trim()

        _state.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            when (
                val result = settingsRepository.update(
                    SettingsUpdateBody(
                        deepseekBaseUrl = baseUrl,
                        deepseekModel = model,
                    ),
                )
            ) {
                is AppResult.Ok -> _state.update {
                    it.copy(saving = false).applyDeepseek(result.value)
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                    return@launch
                }
            }

            if (keyDraft.isEmpty()) {
                emit("已保存")
                return@launch
            }

            _state.update { it.copy(saving = true) }
            when (
                val result = settingsRepository.updateSecrets(
                    SecretsUpdateBody(deepseekApiKey = keyDraft),
                )
            ) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(saving = false, deepseekKeyDraft = "")
                            .applyDeepseek(result.value)
                    }
                    emit("已保存")
                }

                is AppResult.Err -> {
                    // 保留草稿让用户重试；错误文案绝不回显输入值。
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                }
            }
        }
    }

    fun clearDeepseekKey() {
        if (!requireWritable()) return
        _state.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            when (
                val result = settingsRepository.updateSecrets(
                    SecretsUpdateBody(deepseekApiKey = ""),
                )
            ) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(saving = false, deepseekKeyDraft = "")
                            .applyDeepseek(result.value)
                    }
                    emit("已清除 DeepSeek Key")
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                }
            }
        }
    }

    // ---- 语音（DashScope） ----

    fun setDashscopeRegion(value: String) {
        _state.update { it.copy(dashscopeRegion = value) }
    }

    fun setDashscopeAsrModel(value: String) {
        _state.update { it.copy(dashscopeAsrModel = value) }
    }

    fun setDashscopeTtsModel(value: String) {
        _state.update { it.copy(dashscopeTtsModel = value) }
    }

    fun setDashscopeTtsVoice(value: String) {
        _state.update { it.copy(dashscopeTtsVoice = value) }
    }

    fun setDashscopeKeyDraft(value: String) {
        _state.update { it.copy(dashscopeKeyDraft = value) }
    }

    fun setVoiceEnabled(enabled: Boolean) {
        _state.update { it.copy(voiceEnabled = enabled) }
    }

    fun setVoiceProvider(provider: String) {
        _state.update { it.copy(voiceProvider = provider) }
    }

    fun saveVoice() {
        if (!requireWritable()) return
        val current = _state.value
        val keyDraft = current.dashscopeKeyDraft.trim()

        _state.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            when (
                val result = settingsRepository.update(
                    SettingsUpdateBody(
                        voiceEnabled = current.voiceEnabled,
                        voiceProvider = current.voiceProvider,
                        dashscopeRegion = current.dashscopeRegion.trim().takeIf { it.isNotEmpty() },
                        dashscopeAsrModel = current.dashscopeAsrModel.trim().takeIf { it.isNotEmpty() },
                        dashscopeTtsModel = current.dashscopeTtsModel.trim().takeIf { it.isNotEmpty() },
                        dashscopeTtsVoice = current.dashscopeTtsVoice.trim().takeIf { it.isNotEmpty() },
                    ),
                )
            ) {
                is AppResult.Ok -> _state.update {
                    it.copy(saving = false).applyVoice(result.value)
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                    return@launch
                }
            }

            if (keyDraft.isEmpty()) {
                emit("已保存")
                return@launch
            }

            _state.update { it.copy(saving = true) }
            when (
                val result = settingsRepository.updateSecrets(
                    SecretsUpdateBody(dashscopeApiKey = keyDraft),
                )
            ) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(saving = false, dashscopeKeyDraft = "")
                            .applyVoice(result.value)
                    }
                    emit("已保存")
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                }
            }
        }
    }

    fun clearDashscopeKey() {
        if (!requireWritable()) return
        _state.update { it.copy(saving = true, error = null) }
        viewModelScope.launch {
            when (
                val result = settingsRepository.updateSecrets(
                    SecretsUpdateBody(dashscopeApiKey = ""),
                )
            ) {
                is AppResult.Ok -> {
                    _state.update {
                        it.copy(saving = false, dashscopeKeyDraft = "")
                            .applyVoice(result.value)
                    }
                    emit("已清除语音 Key")
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false) }
                    emit(result.error.message)
                }
            }
        }
    }

    // ---- 搜索 ----

    fun setSearchEnabled(enabled: Boolean) {
        if (!requireWritable()) return
        val previous = _state.value.searchEnabled
        _state.update { it.copy(searchEnabled = enabled, saving = true) }
        viewModelScope.launch {
            when (val result = settingsRepository.update(SettingsUpdateBody(searchEnabled = enabled))) {
                is AppResult.Ok -> _state.update {
                    it.copy(
                        saving = false,
                        searchEnabled = result.value.search.enabled,
                    )
                }

                is AppResult.Err -> {
                    _state.update { it.copy(saving = false, searchEnabled = previous) }
                    emit(result.error.message)
                }
            }
        }
    }

    // ---- Token 管理 ----

    fun refreshTokens() {
        if (_state.value.tokensLoading) return
        _state.update { it.copy(tokensLoading = true, tokensError = null) }
        viewModelScope.launch {
            when (val result = tokenRepository.list()) {
                is AppResult.Ok -> _state.update {
                    it.copy(
                        tokens = visibleTokens(result.value),
                        tokensLoading = false,
                        tokensError = null,
                    )
                }

                is AppResult.Err -> _state.update {
                    it.copy(tokensLoading = false, tokensError = result.error.message)
                }
            }
        }
    }

    fun revokeToken(tokenId: String) {
        if (!requireWritable()) return
        if (_state.value.revokingId != null) return
        _state.update { it.copy(revokingId = tokenId) }
        viewModelScope.launch {
            when (val result = tokenRepository.revoke(tokenId)) {
                is AppResult.Ok -> {
                    val removed = _state.value.tokens.filterNot { it.id == tokenId }
                    _state.update { it.copy(revokingId = null, tokens = removed) }
                    if (sessionManager.state.value?.tokenId == tokenId) {
                        // 撤销的是当前设备：本地会话必须立即清空，不能停在已登录界面。
                        sessionManager.clear()
                        _events.emit(SettingsEvent.LoggedOut)
                    } else {
                        emit("已撤销该登录")
                        refreshTokens()
                    }
                }

                is AppResult.Err -> {
                    if (result.error.isTokenMissing()) {
                        // 服务端已没有这条登录（可能是别处刚撤销）：本地列表
                        // 也要同步，不能把死行留着等用户手动刷新。
                        val removed = _state.value.tokens.filterNot { it.id == tokenId }
                        _state.update { it.copy(revokingId = null, tokens = removed) }
                        emit("该登录已不存在或已撤销")
                        refreshTokens()
                    } else {
                        _state.update { it.copy(revokingId = null) }
                        emit(result.error.message)
                    }
                }
            }
        }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    private fun requireWritable(): Boolean {
        if (!isOnline()) {
            emit("当前离线，无法保存")
            return false
        }
        if (_state.value.saving || _state.value.revokingId != null) {
            emit("上一个操作还在进行中")
            return false
        }
        return true
    }

    private fun emit(text: String) {
        _events.tryEmit(SettingsEvent.Message(text))
    }

    private fun SettingsUiState.applyView(view: SettingsDto): SettingsUiState = copy(
        deepseekBaseUrl = view.deepseek.baseUrl,
        deepseekModel = view.deepseek.model,
        deepseekConfigured = view.deepseek.configured,
        deepseekSource = view.deepseek.source,
        dashscopeConfigured = view.dashscope.configured,
        dashscopeSource = view.dashscope.source,
        dashscopeRegion = view.dashscope.region,
        dashscopeAsrModel = view.dashscope.asrModel,
        dashscopeTtsModel = view.dashscope.ttsModel,
        dashscopeTtsVoice = view.dashscope.ttsVoice,
        voiceEnabled = view.voice.enabled,
        voiceProvider = view.voice.provider,
        voiceEffective = view.voice.effective,
        voiceReason = view.voice.reason,
        searchEnabled = view.search.enabled,
    )

    private fun SettingsUiState.applyDeepseek(view: SettingsDto): SettingsUiState = copy(
        deepseekBaseUrl = view.deepseek.baseUrl,
        deepseekModel = view.deepseek.model,
        deepseekConfigured = view.deepseek.configured,
        deepseekSource = view.deepseek.source,
    )

    private fun SettingsUiState.applyVoice(view: SettingsDto): SettingsUiState = copy(
        dashscopeConfigured = view.dashscope.configured,
        dashscopeSource = view.dashscope.source,
        dashscopeRegion = view.dashscope.region,
        dashscopeAsrModel = view.dashscope.asrModel,
        dashscopeTtsModel = view.dashscope.ttsModel,
        dashscopeTtsVoice = view.dashscope.ttsVoice,
        voiceEnabled = view.voice.enabled,
        voiceProvider = view.voice.provider,
        voiceEffective = view.voice.effective,
        voiceReason = view.voice.reason,
    )
}

/**
 * 设备管理只展示仍可用的登录：撤销/过期的行留在服务端审计，但不应出现在
 * 列表里（旧版服务端可能仍返回它们，App 侧再兜一层）。
 */
internal fun visibleTokens(
    tokens: List<AuthTokenDto>,
    nowMillis: Long = System.currentTimeMillis(),
): List<AuthTokenDto> = tokens.filter { token ->
    token.revokedAt == null && token.expiresAt > nowMillis
}

/** 撤销时服务端已没有这条登录：按“已撤销”处理，而不是报错。 */
private fun AppError.isTokenMissing(): Boolean =
    this is AppError.Http && code == "token_not_found"
