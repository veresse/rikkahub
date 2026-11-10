package me.rerere.rikkahub.service.autochat

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.uuid.Uuid

class AutoChatWorker(
    appContext: Context,
    workerParams: WorkerParameters,
    private val executionService: AutoChatExecutionService,
    private val scheduler: AutoChatScheduler,
) : CoroutineWorker(appContext, workerParams) {

    companion object {
        const val KEY_OCCURRENCE_ID = "occurrence_id"
        const val KEY_RULE_ID = "rule_id"

        fun createInputData(occurrenceId: Uuid, ruleId: Uuid) = workDataOf(
            KEY_OCCURRENCE_ID to occurrenceId.toString(),
            KEY_RULE_ID to ruleId.toString(),
        )
    }

    override suspend fun doWork(): Result {
        val occurrenceIdStr = inputData.getString(KEY_OCCURRENCE_ID) ?: return Result.failure()
        val occurrenceId = runCatching { Uuid.parse(occurrenceIdStr) }.getOrNull() ?: return Result.failure()

        val result = executionService.executeOccurrence(occurrenceId, isManualTest = false)

        // 每次执行完毕后幂等更新后续排期
        try {
            scheduler.reconcile()
        } catch (e: Exception) {
            e.printStackTrace()
        }

        return when (result) {
            is AutoChatExecutionResult.Success, is AutoChatExecutionResult.Skipped -> Result.success()
            is AutoChatExecutionResult.Failed -> {
                if (runAttemptCount < 2 && isTransientNetworkError(result.error)) {
                    Result.retry()
                } else {
                    Result.failure()
                }
            }
        }
    }

    private fun isTransientNetworkError(throwable: Throwable): Boolean {
        var current: Throwable? = throwable
        while (current != null) {
            if (current is SocketTimeoutException ||
                current is ConnectException ||
                current is UnknownHostException ||
                current is NoRouteToHostException ||
                (current is IOException && current.message?.contains("timeout", ignoreCase = true) == true)
            ) {
                return true
            }
            current = current.cause
        }
        return false
    }
}
