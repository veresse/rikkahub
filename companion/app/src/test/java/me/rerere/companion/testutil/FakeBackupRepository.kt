package me.rerere.companion.testutil

import java.io.InputStream
import java.io.OutputStream
import kotlinx.coroutines.CompletableDeferred
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.dto.BackupPreviewDto
import me.rerere.companion.data.api.dto.BackupResultDto
import me.rerere.companion.data.repo.BackupRepository

class FakeBackupRepository : BackupRepository {
    var previewResult: AppResult<BackupPreviewDto>? = null
    var confirmResult: AppResult<BackupResultDto>? = null
    var exportResult: AppResult<Long>? = null

    /** 导出时写入的字节，默认空。 */
    var exportBytes: ByteArray = ByteArray(0)

    /** 非空时 preview/confirm 挂起，直到测试 complete，用于验证忙锁。 */
    var gate: CompletableDeferred<Unit>? = null

    var previewCalls: Int = 0
        private set
    var confirmCalls: Int = 0
        private set
    var exportCalls: Int = 0
        private set

    var lastFilename: String? = null
        private set
    var lastPreviewId: String? = null
        private set

    override suspend fun preview(stream: InputStream, filename: String): AppResult<BackupPreviewDto> {
        previewCalls += 1
        lastFilename = filename
        gate?.await()
        return previewResult ?: AppResult.Ok(BackupPreviewDto(previewId = "p1"))
    }

    override suspend fun confirm(previewId: String): AppResult<BackupResultDto> {
        confirmCalls += 1
        lastPreviewId = previewId
        gate?.await()
        return confirmResult ?: AppResult.Ok(BackupResultDto(status = "success"))
    }

    override suspend fun exportTo(dest: OutputStream): AppResult<Long> {
        exportCalls += 1
        dest.write(exportBytes)
        return exportResult ?: AppResult.Ok(exportBytes.size.toLong())
    }
}
