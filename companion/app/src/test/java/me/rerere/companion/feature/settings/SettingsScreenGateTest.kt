package me.rerere.companion.feature.settings

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsScreenGateTest {

    @Test
    fun `settings actions require online and idle`() {
        assertFalse(settingsActionsEnabled(online = false, busy = false))
        assertFalse(settingsActionsEnabled(online = true, busy = true))
        assertTrue(settingsActionsEnabled(online = true, busy = false))
    }
}
