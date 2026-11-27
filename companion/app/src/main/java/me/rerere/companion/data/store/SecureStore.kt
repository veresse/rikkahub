package me.rerere.companion.data.store

import kotlinx.serialization.Serializable

@Serializable
data class StoredSession(
    val serverUrl: String,
    val token: String,
    val tokenId: String,
    val username: String,
    val expiresAtMillis: Long,
)

interface SecureStore {
    suspend fun read(): StoredSession?
    suspend fun write(session: StoredSession)
    suspend fun clear()
}
