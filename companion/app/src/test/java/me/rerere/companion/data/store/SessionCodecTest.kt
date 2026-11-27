package me.rerere.companion.data.store

import me.rerere.companion.data.store.StoredSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SessionCodecTest {

    private val session = StoredSession(
        serverUrl = "https://example.com/",
        token = "t0",
        tokenId = "id0",
        username = "admin",
        expiresAtMillis = 1_700_000_000_000,
    )

    @Test
    fun `round trips session`() {
        assertEquals(session, SessionCodec.decode(SessionCodec.encode(session)))
    }

    @Test
    fun `returns null when required field is missing`() {
        assertNull(SessionCodec.decode("""{"serverUrl":"https://example.com/"}"""))
    }

    @Test
    fun `returns null on malformed json`() {
        assertNull(SessionCodec.decode("not json"))
    }

    @Test
    fun `ignores unknown fields from newer app versions`() {
        val raw = """{"serverUrl":"https://example.com/","token":"t0","tokenId":"id0","username":"admin","expiresAtMillis":1700000000000,"extra":42}"""
        assertEquals(session, SessionCodec.decode(raw))
    }
}
