package me.rerere.companion.data.api

import me.rerere.companion.data.api.dto.LoginRequest
import me.rerere.companion.data.api.dto.LoginResponse
import retrofit2.http.Body
import retrofit2.http.POST

interface AuthApi {
    @POST("api/auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @POST("api/auth/logout")
    suspend fun logout()
}
