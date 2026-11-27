package me.rerere.companion.feature.backup

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.BackupPreviewDto
import me.rerere.companion.data.api.dto.BackupResultDto
import me.rerere.companion.data.api.dto.BackupSummaryDto
import me.rerere.companion.testutil.FakeBackupRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class BackupViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private fun previewDto(id: String = "p1") = BackupPreviewDto(
        previewId = id,
        summary = BackupSummaryDto(assistantCount = 2, conversationCount = 3),
        assistantsSample = listOf("A"),
    )

    private fun viewModel(
        repository: FakeBackupRepository = FakeBackupRepository(),
        online: Boolean = true,
    ) = BackupViewModel(repository, isOnline = { online })

    private fun stream() = ByteArrayInputStream(byteArrayOf(1, 2, 3))

    @Test
    fun `preview then confirm reports imported counts`() = runTest(main.dispatcher) {
        val repo = FakeBackupRepository().apply {
            previewResult = AppResult.Ok(previewDto())
            confirmResult = AppResult.Ok(
                BackupResultDto(status = "success", importedAssistants = 2, importedConversations = 3),
            )
        }
        val vm = viewModel(repo)

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        assertEquals("p1", vm.state.value.preview?.previewId)
        assertEquals("backup.zip", repo.lastFilename)

        vm.confirm()
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertNull(state.preview)
        assertEquals(2, state.result?.importedAssistants)
        assertEquals(3, state.result?.importedConversations)
        assertNull(state.error)
        assertEquals("p1", repo.lastPreviewId)
    }

    @Test
    fun `expired preview clears the stale preview`() = runTest(main.dispatcher) {
        val repo = FakeBackupRepository().apply {
            previewResult = AppResult.Ok(previewDto())
            confirmResult = AppResult.Err(
                AppError.Http("preview_expired", "预览已过期，请重新选择文件"),
            )
        }
        val vm = viewModel(repo)

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        assertNotNull(vm.state.value.preview)

        vm.confirm()
        main.scheduler.advanceUntilIdle()

        // 服务端已拒绝：旧预览不能再重试，必须让用户重新选文件。
        assertNull(vm.state.value.preview)
        assertEquals("预览已过期，请重新选择文件", vm.state.value.error)
    }

    @Test
    fun `network failure while confirming keeps the preview for retry`() = runTest(main.dispatcher) {
        val repo = FakeBackupRepository().apply {
            previewResult = AppResult.Ok(previewDto())
            confirmResult = AppResult.Err(AppError.Network("连接中断"))
        }
        val vm = viewModel(repo)

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()

        vm.confirm()
        main.scheduler.advanceUntilIdle()

        assertEquals("p1", vm.state.value.preview?.previewId)
        assertEquals("连接中断", vm.state.value.error)
        assertFalse(vm.state.value.importing)
    }

    @Test
    fun `export writes to the provided sink and reports size`() = runTest(main.dispatcher) {
        val payload = ByteArray(31_457) { (it % 251).toByte() }
        val repo = FakeBackupRepository().apply {
            exportBytes = payload
            exportResult = AppResult.Ok(payload.size.toLong())
        }
        val vm = viewModel(repo)
        val sink = ByteArrayOutputStream()

        vm.exportTo { sink }
        main.scheduler.advanceUntilIdle()

        assertEquals(payload.size, sink.size())
        assertEquals(payload.size.toLong(), vm.state.value.exportedBytes)
        assertEquals(1, repo.exportCalls)
        assertFalse(vm.state.value.exporting)
    }

    @Test
    fun `busy blocks duplicate imports`() = runTest(main.dispatcher) {
        val gate = CompletableDeferred<Unit>()
        val repo = FakeBackupRepository().apply {
            previewResult = AppResult.Ok(previewDto())
            this.gate = gate
        }
        val vm = viewModel(repo)

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.previewing)

        // 预览还没回来，确认与导出都必须被挡在门外。
        vm.confirm()
        vm.exportTo { ByteArrayOutputStream() }
        main.scheduler.advanceUntilIdle()
        assertEquals(0, repo.confirmCalls)
        assertEquals(0, repo.exportCalls)

        gate.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.previewing)
    }

    @Test
    fun `offline blocks preview confirm and export`() = runTest(main.dispatcher) {
        val repo = FakeBackupRepository()
        val vm = viewModel(repo, online = false)

        vm.preview(::stream)
        vm.confirm()
        vm.exportTo { ByteArrayOutputStream() }
        main.scheduler.advanceUntilIdle()

        assertEquals(0, repo.previewCalls)
        assertEquals(0, repo.confirmCalls)
        assertEquals(0, repo.exportCalls)
        assertEquals("当前离线，无法执行该操作", vm.state.value.error)
    }

    @Test
    fun `unreadable file fails before any network call`() = runTest(main.dispatcher) {
        val repo = FakeBackupRepository()
        val vm = viewModel(repo)

        vm.preview { null }
        main.scheduler.advanceUntilIdle()

        assertEquals(0, repo.previewCalls)
        assertEquals("无法读取所选文件", vm.state.value.error)
        assertFalse(vm.state.value.previewing)
    }
}
