package me.rerere.companion.testutil

import me.rerere.companion.data.store.SecureStore
import me.rerere.companion.data.store.StoredSession

class FakeSecureStore(
    var stored: StoredSession? = null,
) : SecureStore {
    var throwOnRead: Boolean = false
    var clearCount: Int = 0
    var writeCount: Int = 0

    override suspend fun read(): StoredSession? {
        if (throwOnRead) error("keystore unavailable")
        return stored
    }

    override suspend fun write(session: StoredSession) {
        stored = session
        writeCount += 1
    }

    override suspend fun clear() {
        stored = null
        clearCount += 1
    }
}
