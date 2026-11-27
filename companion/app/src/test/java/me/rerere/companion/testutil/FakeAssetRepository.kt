package me.rerere.companion.testutil

import java.io.InputStream
import kotlinx.coroutines.CompletableDeferred
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.AssetFallbackDto
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.data.api.dto.AssetViewDto
import me.rerere.companion.data.repo.AssetRepository

class FakeAssetRepository : AssetRepository {
    var viewResult: AppResult<AssetViewDto> =
        AppResult.Ok(AssetViewDto(fallback = AssetFallbackDto()))
    var previewResult: AppResult<AssetUploadDto> =
        AppResult.Ok(AssetUploadDto(uploadId = "u1"))
    var confirmResult: AppResult<AssetViewDto>? = null
    var rollbackResult: AppResult<AssetViewDto>? = null
    var frameResult: AppResult<ByteArray> = AppResult.Ok(byteArrayOf(1, 2, 3))

    /** 非空时 preview/confirm/rollback 挂起，用于验证忙锁。 */
    var gate: CompletableDeferred<Unit>? = null

    /** 非空时 load() 挂起，用于验证刷新响应的乱序/过期。 */
    var loadGate: CompletableDeferred<Unit>? = null

    /** 非空时 frameBytes 挂起，用于验证首帧请求的过期/乱序响应。 */
    var frameGate: CompletableDeferred<Unit>? = null

    var loadCalls: Int = 0
        private set
    var previewCalls: Int = 0
        private set
    var confirmCalls: Int = 0
        private set
    var rollbackCalls: Int = 0
        private set
    var frameCalls: Int = 0
        private set

    var lastFilename: String? = null
        private set
    var lastUploadId: String? = null
        private set
    var lastFrameId: String? = null
        private set
    var lastFrameIndex: Int? = null
        private set

    override suspend fun load(): AppResult<AssetViewDto> {
        loadCalls += 1
        (loadGate ?: gate)?.await()
        return viewResult
    }

    override suspend fun preview(stream: InputStream, filename: String): AppResult<AssetUploadDto> {
        previewCalls += 1
        lastFilename = filename
        gate?.await()
        return previewResult
    }

    override suspend fun confirm(uploadId: String): AppResult<AssetViewDto> {
        confirmCalls += 1
        lastUploadId = uploadId
        gate?.await()
        return confirmResult ?: viewResult
    }

    override suspend fun rollback(): AppResult<AssetViewDto> {
        rollbackCalls += 1
        gate?.await()
        return rollbackResult ?: viewResult
    }

    override suspend fun frameBytes(packageId: String, index: Int): AppResult<ByteArray> {
        frameCalls += 1
        lastFrameId = packageId
        lastFrameIndex = index
        frameGate?.await()
        return frameResult
    }
}
