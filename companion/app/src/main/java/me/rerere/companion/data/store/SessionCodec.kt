package me.rerere.companion.data.store

import kotlinx.serialization.json.Json

object SessionCodec {
    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    fun encode(session: StoredSession): String = json.encodeToString(StoredSession.serializer(), session)

    fun decode(raw: String): StoredSession? = try {
        json.decodeFromString(StoredSession.serializer(), raw)
    } catch (_: Exception) {
        null
    }
}
