package me.rerere.companion.feature.chat

import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.data.api.dto.MemoryDto
import me.rerere.companion.testutil.FakeMemoryRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class MemoriesViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    @Test
    fun `loads memories and creates a new one`() = runTest {
        val repo = FakeMemoryRepository().apply {
            items = mutableListOf(MemoryDto(id = "m1", assistantId = "a1", content = "喜欢猫"))
        }
        val vm = MemoriesViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()
        assertEquals(listOf("m1"), vm.state.value.items.map { it.id })

        vm.startCreate()
        vm.updateContent("  喜欢狗  ")
        vm.save()
        main.scheduler.advanceUntilIdle()

        // 正文两侧空白必须去掉再提交。
        assertEquals(listOf("a1" to "喜欢狗"), repo.created)
        assertNull(vm.state.value.editor)
        assertEquals(2, vm.state.value.items.size)
    }

    @Test
    fun `editing sends update and keeps original id`() = runTest {
        val repo = FakeMemoryRepository().apply {
            items = mutableListOf(MemoryDto(id = "m1", assistantId = "a1", content = "旧"))
        }
        val vm = MemoriesViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        vm.startEdit(vm.state.value.items.single())
        vm.updateContent("新")
        vm.save()
        main.scheduler.advanceUntilIdle()

        assertEquals(listOf("m1" to "新"), repo.updated)
        assertEquals("新", vm.state.value.items.single().content)
    }

    @Test
    fun `blank content is not saved`() = runTest {
        val repo = FakeMemoryRepository()
        val vm = MemoriesViewModel(repo, assistantId = null)
        main.scheduler.advanceUntilIdle()

        vm.startCreate()
        vm.updateContent("   ")
        vm.save()
        main.scheduler.advanceUntilIdle()

        assertTrue(repo.created.isEmpty())
    }

    @Test
    fun `delete removes item and surfaces error`() = runTest {
        val repo = FakeMemoryRepository().apply {
            items = mutableListOf(MemoryDto(id = "m1", assistantId = "a1", content = "旧"))
        }
        val vm = MemoriesViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        vm.delete("m1")
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.items.isEmpty())
        assertEquals("已删除记忆", vm.state.value.snackbar)

        repo.deleteError = AppError.Network("无法连接服务器")
        vm.delete("m1")
        main.scheduler.advanceUntilIdle()
        assertEquals("无法连接服务器", vm.state.value.snackbar)
    }
}
