package me.rerere.rikkahub.utils

import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class UpdatePollingTest {
    private fun info(version: String) = UpdateInfo(version, "2026-11-20T15:46:28Z", "", emptyList())

    @Test
    fun detectsNewReleaseWithoutRestartingProcess() = runTest {
        var requests = 0
        val releases = pollUpdateInfo(intervalMillis = 1000, retryMillis = 100) {
            info(if (requests++ == 0) "3.3.1.217352194" else "3.3.1.217500000")
        }.filterIsInstance<UiState.Success<UpdateInfo>>().take(2).toList()
        assertEquals(listOf("3.3.1.217352194", "3.3.1.217500000"), releases.map { it.data.version })
        assertEquals(1000L, testScheduler.currentTime)
    }

    @Test
    fun retriesAfterTransientFailure() = runTest {
        var requests = 0
        val states = pollUpdateInfo(intervalMillis = 1000, retryMillis = 100) {
            if (requests++ == 0) error("Network unavailable")
            info("3.3.1.217352194")
        }.take(3).toList()
        assertEquals(true, states[1] is UiState.Error)
        assertEquals(true, states[2] is UiState.Success)
        assertEquals(100L, testScheduler.currentTime)
    }
}
