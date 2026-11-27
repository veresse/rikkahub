package me.rerere.companion.data.session

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import me.rerere.companion.data.store.SecureStore
import me.rerere.companion.data.store.StoredSession

fun interface Clock {
    fun nowMillis(): Long
}

/**
 * 会话的唯一内存态 + 加密存储的串行入口。
 *
 * 所有读写都过 [mutex]：401 清退与新登录保存可能并发，若不加锁会出现
 * “内存已是新 Token、磁盘被旧请求清空”的错位。
 */
class SessionManager(
    private val store: SecureStore,
    private val clock: Clock,
) {
    private val mutex = Mutex()
    private val _state = MutableStateFlow<StoredSession?>(null)
    val state: StateFlow<StoredSession?> = _state.asStateFlow()

    suspend fun restore(): StoredSession? = mutex.withLock {
        val stored = try {
            store.read()
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // 密钥失效、密文损坏或旧格式：清空后回登录，绝不把异常抛给 UI。
            clearLocked()
            return@withLock null
        }
        if (stored == null) return@withLock null
        if (stored.expiresAtMillis <= clock.nowMillis()) {
            clearLocked()
            return@withLock null
        }
        _state.value = stored
        stored
    }

    suspend fun save(session: StoredSession) = mutex.withLock {
        store.write(session)
        _state.value = session
    }

    /**
     * 服务端判定会话失效时调用。
     *
     * [failedToken] 非空时只有当前会话仍是这个 Token 才清退：旧 Token 的迟到 401
     * 不能在用户重新登录之后把新会话清掉；[failedToken] 为空表示无条件清退（登出）。
     *
     * 不依赖“内存态一定存在”：`restore()` 之前磁盘里仍可能有有效 Token，无条件路径
     * 必须一并清掉。带 Token 的 401 只可能发生在会话已加载之后（无 Token 就不发 Bearer）。
     */
    suspend fun invalidate(failedToken: String? = null) = mutex.withLock {
        if (failedToken != null && _state.value?.token != failedToken) return@withLock
        clearLocked()
    }

    suspend fun clear() = mutex.withLock {
        clearLocked()
    }

    private suspend fun clearLocked() {
        store.clear()
        _state.value = null
    }
}
