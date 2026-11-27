package me.rerere.companion.data.session

import kotlinx.coroutines.test.runTest
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SessionManagerTest {

    private val now = 1_000_000L

    private fun session(expiresAtMillis: Long = now + 60_000) = StoredSession(
        serverUrl = "https://example.com/",
        token = "t0",
        tokenId = "id0",
        username = "admin",
        expiresAtMillis = expiresAtMillis,
    )

    private fun manager(store: FakeSecureStore) = SessionManager(store, Clock { now })

    @Test
    fun `restore returns unexpired session`() = runTest {
        val store = FakeSecureStore(session())
        val manager = manager(store)

        assertEquals(session(), manager.restore())
        assertEquals(session(), manager.state.value)
    }

    @Test
    fun `restore treats expiry exactly at now as expired`() = runTest {
        val store = FakeSecureStore(session(expiresAtMillis = now))
        val manager = manager(store)

        assertNull(manager.restore())
        assertNull(manager.state.value)
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `restore clears corrupted store instead of throwing`() = runTest {
        val store = FakeSecureStore(session())
        store.throwOnRead = true
        val manager = manager(store)

        assertNull(manager.restore())
        assertNull(manager.state.value)
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `invalidate always clears persisted session`() = runTest {
        val store = FakeSecureStore(session())
        val manager = manager(store)
        manager.restore()

        manager.invalidate()
        manager.invalidate()

        assertNull(manager.state.value)
        assertNull(store.stored)
        // 重复清退只多一次空删除，换来的是不依赖内存态、不会残留有效 Token。
        assertEquals(2, store.clearCount)
    }

    @Test
    fun `invalidate before restore still clears store`() = runTest {
        val store = FakeSecureStore(session())
        val manager = manager(store)

        manager.invalidate()

        assertNull(manager.state.value)
        assertNull(store.stored)
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `stale token 401 cannot clear a newer session`() = runTest {
        val store = FakeSecureStore(session())
        val manager = manager(store)
        manager.restore()

        manager.invalidate("an-old-token")

        assertEquals(session(), manager.state.value)
        assertEquals(session(), store.stored)
        assertEquals(0, store.clearCount)

        manager.invalidate("t0")

        assertNull(manager.state.value)
        assertEquals(1, store.clearCount)
    }
}
