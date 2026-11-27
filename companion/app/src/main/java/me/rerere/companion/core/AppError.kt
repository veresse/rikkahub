package me.rerere.companion.core

sealed interface AppError {
    val code: String
    val message: String

    data class Http(
        override val code: String,
        override val message: String,
        val retryAfterSeconds: Int? = null,
    ) : AppError

    data class Network(
        override val message: String,
    ) : AppError {
        override val code: String get() = "network"
    }
}

sealed interface AppResult<out T> {
    data class Ok<T>(val value: T) : AppResult<T>
    data class Err(val error: AppError) : AppResult<Nothing>
}
