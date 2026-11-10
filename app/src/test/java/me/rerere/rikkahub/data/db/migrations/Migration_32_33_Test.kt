package me.rerere.rikkahub.data.db.migrations

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class Migration_32_33_Test {

    @Test
    fun `migration object is correctly configured`() {
        assertEquals(32, Migration_32_33.startVersion)
        assertEquals(33, Migration_32_33.endVersion)
    }
}
