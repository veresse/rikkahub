package me.rerere.companion.data.repo

import java.io.InputStream
import java.io.OutputStream
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.CompanionApiFactory
import me.rerere.companion.data.api.StreamRequestBody
import me.rerere.companion.data.api.dto.BackupConfirmBody
import me.rerere.companion.data.api.dto.BackupPreviewDto
import me.rerere.companion.data.api.dto.BackupResultDto
import me.rerere.companion.data.session.SessionManager
import okhttp3.MultipartBody

/**
 * 4D：备份导入/导出。
 *
 * 上传按流写、下载按流读，App 不把整包备份放进内存；预览结果只含统计，
 * 不含备份正文。
 */
interface BackupRepository {
    suspend fun preview(stream: InputStream, filename: String): AppResult<BackupPreviewDto>

    suspend fun confirm(previewId: String): AppResult<BackupResultDto>

    /** 把备份流写入 [dest]；返回写入字节数，[dest] 由调用方负责关闭。 */
    suspend fun exportTo(dest: OutputStream): AppResult<Long>
}

class HttpBackupRepository(
    apiFactory: CompanionApiFactory,
    sessionManager: SessionManager,
) : SessionApi(apiFactory, sessionManager), BackupRepository {

    override suspend fun preview(
        stream: InputStream,
        filename: String,
    ): AppResult<BackupPreviewDto> = call {
        val part = MultipartBody.Part.createFormData(
            "file",
            filename,
            StreamRequestBody({ stream }, length = -1L),
        )
        it.previewBackup(part)
    }

    override suspend fun confirm(previewId: String): AppResult<BackupResultDto> =
        call { it.confirmBackup(BackupConfirmBody(previewId)) }

    override suspend fun exportTo(dest: OutputStream): AppResult<Long> = call {
        it.exportBackup().use { body ->
            body.byteStream().use { input ->
                input.copyTo(dest, DEFAULT_BUFFER_SIZE)
            }
        }
    }
}
