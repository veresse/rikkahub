package me.rerere.companion.feature.assets

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AssetsScreenGateTest {

    @Test
    fun `asset actions require online and idle`() {
        assertFalse(assetActionsEnabled(online = false, busy = false))
        assertFalse(assetActionsEnabled(online = true, busy = true))
        assertFalse(assetActionsEnabled(online = false, busy = true))
        assertTrue(assetActionsEnabled(online = true, busy = false))
    }
}
