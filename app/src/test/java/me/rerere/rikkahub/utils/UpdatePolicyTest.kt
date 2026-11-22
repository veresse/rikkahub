package me.rerere.rikkahub.utils

import org.junit.Assert.*
import org.junit.Test

class UpdatePolicyTest {
    private fun apk(name: String) = UpdateDownload(name, "https://github.com/veresse/rikkahub/releases/download/v1/$name", "1 MB")

    @Test fun selectsDeviceAbiBeforeUniversal() {
        val arm = apk("app-github-arm64-v8a-release.apk")
        val x86 = apk("app-github-x86_64-release.apk")
        val universal = apk("app-github-universal-release.apk")
        assertEquals(arm, selectUpdateDownload(listOf(x86, universal, arm), listOf("arm64-v8a")))
        assertEquals(x86, selectUpdateDownload(listOf(arm, x86), listOf("x86_64", "arm64-v8a")))
        assertEquals(universal, selectUpdateDownload(listOf(arm, universal), listOf("armeabi-v7a")))
        assertNull(selectUpdateDownload(listOf(arm), listOf("x86_64")))
    }

    @Test fun respectsChannelOnboardingAndSnooze() {
        assertTrue(shouldCheckForUpdate(false, false, 100, 100))
        assertFalse(shouldCheckForUpdate(true, false, 0, 100))
        assertFalse(shouldCheckForUpdate(false, true, 0, 100))
        assertFalse(shouldCheckForUpdate(false, false, 101, 100))
    }
}
