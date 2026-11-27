package me.rerere.companion.feature.assistants

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import me.rerere.companion.core.AppError
import me.rerere.companion.data.api.dto.AssistantDto
import me.rerere.companion.testutil.FakeAssistantRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class AssistantEditViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    @Test
    fun `new assistant sends model and search mode`() = runTest {
        val repo = FakeAssistantRepository()
        val vm = AssistantEditViewModel(repo, assistantId = null)

        vm.update {
            it.copy(
                name = "夏以昼",
                modelId = "deepseek-chat",
                webSearchMode = "on",
            )
        }
        vm.save()
        main.scheduler.advanceUntilIdle()

        val draft = repo.created.single()
        assertEquals("夏以昼", draft.name)
        assertEquals("deepseek-chat", draft.modelId)
        assertEquals("on", draft.webSearchMode)
    }

    @Test
    fun `blank name blocks repository call`() = runTest {
        val repo = FakeAssistantRepository()
        val vm = AssistantEditViewModel(repo, assistantId = null)

        vm.save()
        main.scheduler.advanceUntilIdle()

        assertTrue(repo.created.isEmpty())
        assertEquals("请填写角色名称", vm.state.value.error)
    }

    @Test
    fun `invalid temperature is rejected before repository`() = runTest {
        val repo = FakeAssistantRepository()
        val vm = AssistantEditViewModel(repo, assistantId = null)

        vm.update { it.copy(name = "角色", temperature = "3") }
        vm.save()
        main.scheduler.advanceUntilIdle()

        assertTrue(repo.created.isEmpty())
        assertEquals("温度必须在 0 到 2 之间", vm.state.value.error)
    }

    @Test
    fun `edit loads existing assistant fields`() = runTest {
        val repo = FakeAssistantRepository().apply {
            detail = AssistantDto(
                id = "a1",
                name = "夏以昼",
                systemPrompt = "人设",
                modelId = "deepseek-reasoner",
                contextMessageLimit = 30,
                temperature = 0.4,
                topP = 0.8,
                enableMemory = true,
                webSearchMode = "auto",
            )
        }
        val vm = AssistantEditViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals(false, state.loading)
        assertEquals("夏以昼", state.name)
        assertEquals("deepseek-reasoner", state.modelId)
        assertEquals("30", state.contextMessageLimit)
        assertEquals("0.4", state.temperature)
        assertEquals("true", state.enableMemory.toString())
        assertEquals("auto", state.webSearchMode)
    }

    @Test
    fun `load failure blocks save and retry can recover`() = runTest {
        val repo = FakeAssistantRepository().apply {
            detailError = AppError.Network("无法连接服务器")
        }
        val vm = AssistantEditViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.loadFailed)

        // 空表单 + 默认值保存会覆盖原角色，必须被挡住。
        vm.save()
        main.scheduler.advanceUntilIdle()
        assertTrue(repo.updated.isEmpty())

        repo.detailError = null
        repo.detail = AssistantDto(id = "a1", name = "角色")
        vm.retryLoad()
        main.scheduler.advanceUntilIdle()
        assertEquals(false, vm.state.value.loadFailed)
        assertEquals("角色", vm.state.value.name)
    }

    @Test
    fun `editing preserves avatar when saving`() = runTest {
        val avatar = Json.decodeFromString<JsonObject>(
            """{"type":"emoji","content":"cat"}""",
        )
        val repo = FakeAssistantRepository().apply {
            detail = AssistantDto(id = "a1", name = "角色", avatar = avatar)
        }
        val vm = AssistantEditViewModel(repo, assistantId = "a1")
        main.scheduler.advanceUntilIdle()

        vm.save()
        main.scheduler.advanceUntilIdle()

        assertEquals(avatar, repo.updated.single().second.avatar)
    }

    @Test
    fun `save failure shows readable error`() = runTest {
        val repo = FakeAssistantRepository().apply {
            saveError = AppError.Http("validation_error", "请求参数不合法")
        }
        val vm = AssistantEditViewModel(repo, assistantId = null)

        vm.update { it.copy(name = "角色") }
        vm.save()
        main.scheduler.advanceUntilIdle()

        assertEquals("请求参数不合法", vm.state.value.error)
        assertEquals(false, vm.state.value.saving)
    }
}
