package me.rerere.companion.feature.assistants

import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.testutil.FakeAssistantRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class AssistantsViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    @Test
    fun `loads assistants on start`() = runTest {
        val repo = FakeAssistantRepository().apply {
            items = mutableListOf(AssistantDto(id = "a1", name = "夏以昼"))
        }
        val vm = AssistantsViewModel(repo)
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("a1"), vm.state.value.items.map { it.id })
        assertEquals(false, vm.state.value.loading)
        assertNull(vm.state.value.error)
    }

    @Test
    fun `list failure shows readable error`() = runTest {
        val repo = FakeAssistantRepository().apply {
            listError = AppError.Network("无法连接服务器")
        }
        val vm = AssistantsViewModel(repo)
        main.scheduler.advanceUntilIdle()

        assertEquals("无法连接服务器", vm.state.value.error)
        assertTrue(vm.state.value.items.isEmpty())
    }

    @Test
    fun `delete success removes item`() = runTest {
        val repo = FakeAssistantRepository().apply {
            items = mutableListOf(
                AssistantDto(id = "a1", name = "夏以昼"),
                AssistantDto(id = "a2", name = "别人"),
            )
        }
        val vm = AssistantsViewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.delete("a1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("a2"), vm.state.value.items.map { it.id })
        assertEquals(listOf("a1"), repo.deleted)
        assertNull(vm.state.value.deletingId)
    }

    @Test
    fun `delete failure keeps item and emits message`() = runTest {
        val repo = FakeAssistantRepository().apply {
            items = mutableListOf(AssistantDto(id = "a1", name = "夏以昼"))
            deleteError = AppError.Http("device_offline", "设备不在线")
        }
        val vm = AssistantsViewModel(repo)
        main.scheduler.advanceUntilIdle()

        val messages = mutableListOf<AssistantsEvent>()
        val job = kotlinx.coroutines.CoroutineScope(main.dispatcher).launch {
            vm.events.collect { messages += it }
        }
        main.scheduler.runCurrent()

        vm.delete("a1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("a1"), vm.state.value.items.map { it.id })
        assertTrue(messages.any { it is AssistantsEvent.Message })
        job.cancel()
    }
}
