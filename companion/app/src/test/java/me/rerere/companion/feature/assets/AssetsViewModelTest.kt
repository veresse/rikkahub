package me.rerere.companion.feature.assets

import java.io.ByteArrayInputStream
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppError
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssetFallbackDto
import me.rerere.companion.data.api.dto.AssetPackageDto
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.data.api.dto.AssetViewDto
import me.rerere.companion.testutil.FakeAssetRepository
import me.rerere.companion.testutil.MainDispatcherRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class AssetsViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private fun asset(id: String = "a1", version: String = "1.0.0") = AssetPackageDto(
        id = id,
        name = "Q版陪伴",
        version = version,
        fps = 12,
        frameCount = 2,
        sizeBytes = 1024,
        sha256 = "abc",
    )

    private fun view(active: AssetPackageDto? = null, previous: AssetPackageDto? = null) =
        AssetViewDto(
            active = active,
            previous = previous,
            fallback = AssetFallbackDto(),
        )

    private fun viewModel(
        repository: FakeAssetRepository = FakeAssetRepository(),
        online: Boolean = true,
    ) = AssetsViewModel(repository, isOnline = { online })

    private fun stream() = ByteArrayInputStream(byteArrayOf(1, 2, 3))

    @Test
    fun `load maps active previous and fallback`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            viewResult = AppResult.Ok(view(active = asset("a2"), previous = asset("a1")))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertFalse(state.loading)
        assertEquals("a2", state.active?.id)
        assertEquals("a1", state.previous?.id)
        assertEquals("builtin", state.fallback.version)
        assertNull(state.error)
    }

    @Test
    fun `preview stores the validated upload`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Ok(AssetUploadDto(uploadId = "u9", version = "2.0.0"))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()

        assertEquals("u9", vm.state.value.preview?.uploadId)
        assertEquals("assets.zip", repo.lastFilename)
        assertFalse(vm.state.value.uploading)
    }

    @Test
    fun `confirm updates active and clears preview`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Ok(AssetUploadDto(uploadId = "u1"))
            confirmResult = AppResult.Ok(view(active = asset("a2", "2.0.0"), previous = asset("a1")))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        vm.confirm()
        main.scheduler.advanceUntilIdle()

        val state = vm.state.value
        assertEquals("u1", repo.lastUploadId)
        assertEquals("a2", state.active?.id)
        assertEquals("a1", state.previous?.id)
        assertNull(state.preview)
        assertNull(state.error)
    }

    @Test
    fun `invalid package surfaces the readable message`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Err(
                AppError.Http("invalid_frame", "素材帧必须是 240×240 的 PNG"),
            )
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()

        assertEquals("素材帧必须是 240×240 的 PNG", vm.state.value.error)
        assertNull(vm.state.value.preview)
        assertFalse(vm.state.value.uploading)
    }

    @Test
    fun `expired upload drops the stale preview`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Ok(AssetUploadDto(uploadId = "u1"))
            confirmResult = AppResult.Err(
                AppError.Http("upload_expired", "上传已过期，请重新选择文件"),
            )
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        vm.confirm()
        main.scheduler.advanceUntilIdle()

        assertNull(vm.state.value.preview)
        assertEquals("上传已过期，请重新选择文件", vm.state.value.error)
    }

    @Test
    fun `rollback restores the previous package`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            viewResult = AppResult.Ok(view(active = asset("a2"), previous = asset("a1")))
            rollbackResult = AppResult.Ok(view(active = asset("a1")))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.rollback()
        main.scheduler.advanceUntilIdle()

        assertEquals("a1", vm.state.value.active?.id)
        assertNull(vm.state.value.previous)
        assertEquals(1, repo.rollbackCalls)
    }

    @Test
    fun `preview image uses active frame and falls back to placeholder`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            viewResult = AppResult.Ok(view())
            frameResult = AppResult.Ok(byteArrayOf(9, 8, 7))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()

        assertEquals("fallback", repo.lastFrameId)
        assertEquals(0, repo.lastFrameIndex)
        assertEquals(listOf<Byte>(9, 8, 7), vm.state.value.previewBytes?.toList())
    }

    @Test
    fun `oversized preview image is dropped`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            frameResult = AppResult.Ok(ByteArray(MAX_PREVIEW_BYTES + 1))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()

        assertNull(vm.state.value.previewBytes)
        assertEquals("首帧超过 1 MiB，已跳过预览", vm.state.value.error)
    }

    @Test
    fun `stale preview image is dropped when active package changes`() = runTest(main.dispatcher) {
        val gate = CompletableDeferred<Unit>()
        val repo = FakeAssetRepository().apply {
            viewResult = AppResult.Ok(view(active = asset("a1")))
            frameResult = AppResult.Ok(byteArrayOf(1, 1, 1))
            frameGate = gate
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()
        assertEquals("a1", repo.lastFrameId)

        // active 在首帧返回前切到 a2：旧请求的响应不能显示成新包的首帧。
        repo.viewResult = AppResult.Ok(view(active = asset("a2")))
        repo.frameGate = null
        vm.load()
        main.scheduler.advanceUntilIdle()
        assertEquals("a2", vm.state.value.active?.id)
        assertNull(vm.state.value.previewBytes)

        gate.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertNull(vm.state.value.previewBytes)

        repo.frameResult = AppResult.Ok(byteArrayOf(2, 2, 2))
        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()
        assertEquals("a2", repo.lastFrameId)
        assertEquals(listOf<Byte>(2, 2, 2), vm.state.value.previewBytes?.toList())
    }

    @Test
    fun `preview image success does not clear an unrelated error`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Err(AppError.Http("invalid_frame", "素材帧必须是 240×240 的 PNG"))
            frameResult = AppResult.Ok(byteArrayOf(9))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        assertEquals("素材帧必须是 240×240 的 PNG", vm.state.value.error)

        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()

        assertEquals("素材帧必须是 240×240 的 PNG", vm.state.value.error)
        assertEquals(listOf<Byte>(9), vm.state.value.previewBytes?.toList())
    }

    @Test
    fun `stale refresh response does not overwrite a newer confirmed package`() =
        runTest(main.dispatcher) {
            val gate = CompletableDeferred<Unit>()
            val repo = FakeAssetRepository().apply {
                viewResult = AppResult.Ok(view(active = asset("a1")))
                previewResult = AppResult.Ok(AssetUploadDto(uploadId = "u1"))
                confirmResult =
                    AppResult.Ok(view(active = asset("a2"), previous = asset("a1")))
            }
            val vm = viewModel(repo)
            main.scheduler.advanceUntilIdle()
            assertEquals("a1", vm.state.value.active?.id)

            // 刷新请求还没回来（返回的是旧的 a1 快照），此时确认得到 a2。
            repo.loadGate = gate
            vm.load()
            main.scheduler.advanceUntilIdle()
            repo.loadGate = null
            vm.preview(::stream)
            main.scheduler.advanceUntilIdle()
            vm.confirm()
            main.scheduler.advanceUntilIdle()
            assertEquals("a2", vm.state.value.active?.id)

            gate.complete(Unit)
            main.scheduler.advanceUntilIdle()

            assertEquals("a2", vm.state.value.active?.id)
            assertFalse(vm.state.value.loading)
        }

    @Test
    fun `failed preview image keeps the previously shown frame`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository().apply {
            viewResult = AppResult.Ok(view(active = asset("a1")))
            frameResult = AppResult.Ok(byteArrayOf(4, 5, 6))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()
        assertEquals(listOf<Byte>(4, 5, 6), vm.state.value.previewBytes?.toList())

        // 同一个包的瞬时网络失败：保留已显示的首帧，只报错。
        repo.frameResult = AppResult.Err(AppError.Http("asset_frame_not_found", "素材帧不存在"))
        vm.loadPreviewImage()
        main.scheduler.advanceUntilIdle()

        assertEquals("素材帧不存在", vm.state.value.error)
        assertEquals(listOf<Byte>(4, 5, 6), vm.state.value.previewBytes?.toList())
    }

    @Test
    fun `offline blocks preview confirm and rollback`() = runTest(main.dispatcher) {
        val repo = FakeAssetRepository()
        val vm = viewModel(repo, online = false)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        vm.confirm()
        vm.rollback()
        main.scheduler.advanceUntilIdle()

        assertEquals(0, repo.loadCalls)
        assertEquals(0, repo.previewCalls)
        assertEquals(0, repo.confirmCalls)
        assertEquals(0, repo.rollbackCalls)
        assertEquals("当前离线，无法执行该操作", vm.state.value.error)
    }

    @Test
    fun `busy confirm blocks rollback`() = runTest(main.dispatcher) {
        val gate = CompletableDeferred<Unit>()
        val repo = FakeAssetRepository().apply {
            previewResult = AppResult.Ok(AssetUploadDto(uploadId = "u1"))
        }
        val vm = viewModel(repo)
        main.scheduler.advanceUntilIdle()

        vm.preview(::stream)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.uploading)

        // 预览已完成；现在让确认挂起，回滚必须被忙锁挡住。
        repo.gate = gate
        vm.confirm()
        main.scheduler.advanceUntilIdle()
        assertTrue(vm.state.value.confirming)

        vm.rollback()
        main.scheduler.advanceUntilIdle()
        assertEquals(0, repo.rollbackCalls)

        gate.complete(Unit)
        main.scheduler.advanceUntilIdle()
        assertFalse(vm.state.value.confirming)
    }
}
