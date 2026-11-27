package me.rerere.companion.feature.backup

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackupScreenGateTest {

    @Test
    fun `backup actions require online and idle`() {
        assertFalse(backupActionsEnabled(online = false, busy = false))
        assertFalse(backupActionsEnabled(online = true, busy = true))
        assertFalse(backupActionsEnabled(online = false, busy = true))
        assertTrue(backupActionsEnabled(online = true, busy = false))
    }
}
