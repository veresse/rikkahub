package me.rerere.companion.feature.login

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlin.math.ceil
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.core.UrlCheck
import me.rerere.companion.core.UrlError
import me.rerere.companion.core.UrlValidator
import me.rerere.companion.data.repo.AuthRepository

data class LoginUiState(
    val serverUrl: String = "",
    val username: String = "admin",
    val password: String = "",
    val submitting: Boolean = false,
    val error: String? = null,
    val urlError: UrlError? = null,
    val retryAfterSeconds: Int = 0,
    val canSubmit: Boolean = true,
)

class LoginViewModel(
    private val repository: AuthRepository,
    private val allowCleartext: Boolean,
    /**
     * 单调时钟（默认 `SystemClock.elapsedRealtime()`，含深睡时间）。
     *
     * 倒计时按“截止时刻”算而不是“减 tick”：进程在后台被冻结时 tick 会停，
     * 但时间仍在走，恢复后第一次 tick 就能立即纠正剩余秒数。
     */
    private val elapsedMillis: () -> Long = { SystemClock.elapsedRealtime() },
    private val ticker: suspend (Long) -> Unit = { delay(it) },
) : ViewModel() {

    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun onServerUrlChange(value: String) {
        _state.update { it.copy(serverUrl = value, urlError = null) }
    }

    fun onUsernameChange(value: String) {
        _state.update { it.copy(username = value) }
    }

    fun onPasswordChange(value: String) {
        _state.update { it.copy(password = value) }
    }

    fun submit() {
        val snapshot = _state.value
        if (snapshot.submitting || !snapshot.canSubmit) return

        val normalized = when (val check = UrlValidator.validate(snapshot.serverUrl, allowCleartext)) {
            is UrlCheck.Invalid -> {
                _state.update { it.copy(urlError = check.reason, error = null) }
                return
            }

            is UrlCheck.Ok -> check.normalized
        }

        _state.update {
            it.copy(
                serverUrl = normalized,
                submitting = true,
                error = null,
                urlError = null,
            )
        }

        viewModelScope.launch {
            val result = try {
                repository.login(normalized, snapshot.username, snapshot.password)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                AppResult.Err(AppError.Network("登录失败，请稍后重试"))
            }

            when (result) {
                is AppResult.Ok -> _state.update {
                    // VM 是 Activity 作用域：成功后立刻清口令，避免登出回登录页时回填。
                    it.copy(submitting = false, password = "")
                }

                is AppResult.Err -> {
                    val error = result.error
                    val retrySeconds = (error as? AppError.Http)?.retryAfterSeconds
                    if (error.code == "rate_limited" && retrySeconds != null && retrySeconds > 0) {
                        _state.update {
                            it.copy(
                                submitting = false,
                                error = error.message,
                                retryAfterSeconds = retrySeconds.coerceAtMost(MAX_RETRY_SECONDS),
                                canSubmit = false,
                            )
                        }
                        countdown(retrySeconds)
                    } else {
                        _state.update {
                            it.copy(submitting = false, error = error.message)
                        }
                    }
                }
            }
        }
    }

    /**
     * `Retry-After` 是最坏情况上界；按 [elapsedMillis] 截止时刻每秒重算，归零即恢复可点。
     * 服务端提前解除限速时，下一次请求自然会成功。超过一小时的值按一小时展示，避免
     * 服务端配置异常导致界面永远不可点。
     */
    private suspend fun countdown(seconds: Int) {
        val capped = seconds.coerceIn(1, MAX_RETRY_SECONDS)
        val deadline = elapsedMillis() + capped * 1_000L
        val maxTicks = (capped * 1_000L / TICK_MILLIS).toInt() + 8

        repeat(maxTicks) {
            val remainingMillis = (deadline - elapsedMillis()).coerceAtLeast(0L)
            val remaining = ceil(remainingMillis / 1_000.0).toInt()
            _state.update {
                it.copy(retryAfterSeconds = remaining, canSubmit = remaining == 0)
            }
            if (remaining == 0) return
            ticker(TICK_MILLIS)
        }

        // 时钟被系统异常回拨等极端情况：保证出口一定恢复可点。
        _state.update { it.copy(retryAfterSeconds = 0, canSubmit = true) }
    }

    private companion object {
        const val TICK_MILLIS = 250L
        const val MAX_RETRY_SECONDS = 3_600
    }
}
