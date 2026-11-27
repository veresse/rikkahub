package me.rerere.companion.data.api

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.serialization.json.Json
import me.rerere.companion.data.store.StoredSession
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

fun interface AuthApiFactory {
    fun create(baseUrl: String): AuthApi
}

fun interface CompanionApiFactory {
    fun createCompanion(baseUrl: String): CompanionApi

    /**
     * SSE/长连接专用：默认与普通请求一致；Retrofit 实现会去掉读超时。
     * 具备默认实现，避免测试假工厂被迫实现。
     */
    fun createStreaming(baseUrl: String): CompanionApi = createCompanion(baseUrl)
}

/**
 * 为给定服务器地址构建 Retrofit 客户端。
 *
 * 需要在服务器地址变化或重新登录后重建：拦截器持有当时的会话读取器。
 * 401 去重按“已通知过的 Token”记录，多个客户端共享；新 Token 会重新获得一次清退机会。
 */
class RetrofitApiFactory(
    private val sessionProvider: () -> StoredSession?,
    private val onUnauthorized: (token: String) -> Unit,
) : AuthApiFactory, CompanionApiFactory {

    private val notifiedToken = AtomicReference<String?>(null)

    /**
     * 按 `baseUrl + 读超时` 复用 Retrofit/OkHttp 实例：每个请求新建 client 会
     * 各自持有连接池、Dispatcher 与清理线程，TLS 也无法复用。
     */
    private val retrofitCache = ConcurrentHashMap<String, Retrofit>()

    override fun create(baseUrl: String): AuthApi =
        retrofit(baseUrl, DEFAULT_READ_TIMEOUT_SECONDS).create(AuthApi::class.java)

    override fun createCompanion(baseUrl: String): CompanionApi =
        retrofit(baseUrl, DEFAULT_READ_TIMEOUT_SECONDS).create(CompanionApi::class.java)

    override fun createStreaming(baseUrl: String): CompanionApi =
        retrofit(baseUrl, STREAM_IDLE_TIMEOUT_SECONDS).create(CompanionApi::class.java)

    private fun retrofit(baseUrl: String, readTimeoutSeconds: Long): Retrofit =
        retrofitCache.computeIfAbsent("$readTimeoutSeconds|$baseUrl") {
            buildRetrofit(baseUrl, readTimeoutSeconds)
        }

    private fun buildRetrofit(
        baseUrl: String,
        readTimeoutSeconds: Long = DEFAULT_READ_TIMEOUT_SECONDS,
    ): Retrofit {
        val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSeconds, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(
                AuthInterceptor(
                    tokenProvider = { sessionProvider()?.token },
                    isLoginRequest = { path -> path.endsWith(LOGIN_PATH) },
                    notifiedToken = notifiedToken,
                    onUnauthorized = onUnauthorized,
                ),
            )
            .build()

        // encodeDefaults=true：`device_name` 与默认值相同时也必须发出，否则服务端记成空设备名。
        val json = Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
        }
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
    }

    private companion object {
        const val LOGIN_PATH = "api/auth/login"
        const val DEFAULT_READ_TIMEOUT_SECONDS = 15L

        /**
         * SSE 空闲超时：120s 内没有任何字节就放弃，避免模型/上游挂起时永久转圈。
         * 不用 0（无限等）：用户点停止或退出页面时虽然会 `Call.cancel()`，
         * 但对端半开连接等场景仍需要兜底。
         */
        const val STREAM_IDLE_TIMEOUT_SECONDS = 120L
    }
}
