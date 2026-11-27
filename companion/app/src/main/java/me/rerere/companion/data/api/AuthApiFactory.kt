package me.rerere.companion.data.api

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

/**
 * 为给定服务器地址构建 Retrofit 客户端。
 *
 * 需要在服务器地址变化或重新登录后重建：拦截器持有当时的会话读取器。
 * 401 去重按“已通知过的 Token”记录，多个客户端共享；新 Token 会重新获得一次清退机会。
 */
class RetrofitAuthApiFactory(
    private val sessionProvider: () -> StoredSession?,
    private val onUnauthorized: (token: String) -> Unit,
) : AuthApiFactory {

    private val notifiedToken = AtomicReference<String?>(null)

    override fun create(baseUrl: String): AuthApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
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
            .create(AuthApi::class.java)
    }

    private companion object {
        const val LOGIN_PATH = "api/auth/login"
    }
}
