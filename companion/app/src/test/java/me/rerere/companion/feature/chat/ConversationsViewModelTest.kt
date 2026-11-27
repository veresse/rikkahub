package me.rerere.companion.feature.chat

import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.data.api.dto.ConversationDto
import me.rerere.companion.testutil.FakeConversationRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ConversationsViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    @Test
    fun `loads conversations on start`() = runTest {
        val repo = FakeConversationRepository()
        repo.items = mutableListOf(
            ConversationDto(id = "c1", assistantId = "a1", title = "第一次聊天"),
        )
        val vm = ConversationsViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("c1"), vm.state.value.items.map { it.id })
        assertEquals(false, vm.state.value.loading)
    }

    @Test
    fun `create emits created event for navigation`() = runTest(main.dispatcher) {
        val repo = FakeConversationRepository()
        val vm = ConversationsViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        val created = async(start = CoroutineStart.UNDISPATCHED) {
            vm.events.filterIsInstance<ConversationsEvent.Created>().first()
        }

        vm.create()
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("a1"), repo.created)
        assertEquals("c-1", created.await().conversationId)
        assertEquals(listOf("c-1"), vm.state.value.items.map { it.id })
    }

    @Test
    fun `delete removes item and reports failure`() = runTest(main.dispatcher) {
        val repo = FakeConversationRepository()
        repo.items = mutableListOf(ConversationDto(id = "c1", assistantId = "a1"))
        val vm = ConversationsViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        val success = async(start = CoroutineStart.UNDISPATCHED) {
            vm.events.filterIsInstance<ConversationsEvent.Message>().first()
        }
        vm.delete("c1")
        main.scheduler.advanceUntilIdle()

        assertTrue(vm.state.value.items.isEmpty())
        assertEquals("已删除对话", success.await().text)

        repo.deleteError = AppError.Network("无法连接服务器")
        val failure = async(start = CoroutineStart.UNDISPATCHED) {
            vm.events.filterIsInstance<ConversationsEvent.Message>().first()
        }
        vm.delete("c1")
        main.scheduler.advanceUntilIdle()

        assertEquals("无法连接服务器", failure.await().text)
    }

    @Test
    fun `stale list does not resurrect a deleted conversation`() = runTest(main.dispatcher) {
        val repo = FakeConversationRepository()
        repo.items = mutableListOf(ConversationDto(id = "c1", assistantId = "a1"))
        val vm = ConversationsViewModel(repo, assistantId = "a1", autoLoad = false)

        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        repo.listGate = gate
        vm.refresh()
        main.scheduler.advanceUntilIdle()

        // 列表请求还在途时删除成功；它返回的是删除前的快照。
        repo.listGate = null
        vm.delete("c1")
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.items.isEmpty())
        // 删除成功必须立刻解除 loading；否则在途列表被作废后页面会一直转圈。
        assertEquals(false, vm.state.value.loading)

        gate.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.items.isEmpty())
        assertEquals(false, vm.state.value.loading)
    }

    @Test
    fun `list failure shows error`() = runTest {
        val repo = FakeConversationRepository().apply {
            listError = AppError.Network("断网了")
        }
        val vm = ConversationsViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        assertEquals("断网了", vm.state.value.error)
        assertEquals(false, vm.state.value.loading)
    }
}
