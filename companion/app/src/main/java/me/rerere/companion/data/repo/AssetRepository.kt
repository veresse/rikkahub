package me.rerere.companion.data.repo

import java.io.ByteArrayOutputStream
import java.io.InputStream
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.StreamRequestBody
import me.rerere.companion.data.api.dto.AssetConfirmBody
import me.rerere.companion.data.api.dto.AssetUploadDto
import me.rerere.companion.data.api.dto.AssetViewDto
import me.rerere.companion.data.session.SessionManager
import okhttp3.MultipartBody

/** 单帧响应上限：服务端已限 512 KiB，这里再兜一层防止异常代理撑爆内存。 */
const val MAX_ASSET_FRAME_BYTES = 1024 * 1024

/**
 * 4D：素材包管理。
 *
 * 上传按流写；帧按需取字节（服务端已限制单帧 512 KiB）。
 * 仓库不缓存包内容，也不拼接服务器路径。
 */
interface AssetRepository {
    suspend fun load(): AppResult<AssetViewDto>

    suspend fun preview(stream: InputStream, filename: String): AppResult<AssetUploadDto>

    suspend fun confirm(uploadId: String): AppResult<AssetViewDto>

    suspend fun rollback(): AppResult<AssetViewDto>

    suspend fun frameBytes(packageId: String, index: Int): AppResult<ByteArray>
}

class HttpAssetRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), AssetRepository {

    override suspend fun load(): AppResult<AssetViewDto> = call { it.getAssets() }

    override suspend fun preview(
        stream: InputStream,
        filename: String,
    ): AppResult<AssetUploadDto> = call {
        val part = MultipartBody.Part.createFormData(
            "file",
            filename,
            StreamRequestBody({ stream }, length = -1L),
        )
        it.previewAsset(part)
    }

    override suspend fun confirm(uploadId: String): AppResult<AssetViewDto> =
        call { it.confirmAsset(AssetConfirmBody(uploadId)) }

    override suspend fun rollback(): AppResult<AssetViewDto> = call { it.rollbackAssets() }

    override suspend fun frameBytes(packageId: String, index: Int): AppResult<ByteArray> =
        call {
            it.assetFrame(packageId, index).use { body ->
                // 不能 `body.bytes()`：恶意/异常响应会整包进内存。只读到上限
                // 多 1 字节，交给调用方判断“超过 1 MiB”。
                body.byteStream().use { it.readAtMost(MAX_ASSET_FRAME_BYTES + 1) }
            }
        }
}

/** 最多读取 [limit] 字节；到上限即停止，剩下的不读。 */
private fun InputStream.readAtMost(limit: Int): ByteArray {
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(16 * 1024)
    while (out.size() < limit) {
        val read = read(buffer, 0, minOf(buffer.size, limit - out.size()))
        if (read < 0) break
        out.write(buffer, 0, read)
    }
    return out.toByteArray()
}
