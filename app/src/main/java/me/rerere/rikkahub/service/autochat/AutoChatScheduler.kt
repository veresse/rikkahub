package me.rerere.rikkahub.service.autochat

import android.content.Context
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import me.rerere.rikkahub.data.autochat.AutoChatTimeEngine
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import me.rerere.rikkahub.data.repository.AutoChatRepository
import java.time.LocalDate
import java.util.concurrent.TimeUnit
import kotlin.uuid.Uuid

private const val TAG = "AutoChatScheduler"
const val TAG_AUTO_CHAT = "tag_auto_chat"

class AutoChatScheduler(
    private val context: Context,
    private val repository: AutoChatRepository,
    private val timeEngine: AutoChatTimeEngine = AutoChatTimeEngine(),
    private val lazyExecutionService: Lazy<AutoChatExecutionService>,
) {
    private val mutex = Mutex()

    /**
     * 协调所有启用规则的未来触发任务（幂等），并在 WorkManager 中登记
     */
    suspend fun reconcile(): Unit = withContext(Dispatchers.IO) {
        mutex.withLock {
            try {
                val enabledRules = repository.getEnabledRules()
                val today = timeEngine.getTodayDate()
                val tomorrow = today.plusDays(1)
                val nowMillis = timeEngine.nowMillis()

                val workManager = WorkManager.getInstance(context)

                for (rule in enabledRules) {
                    scheduleRuleForDate(rule, today, nowMillis, workManager)
                    scheduleRuleForDate(rule, tomorrow, nowMillis, workManager)
                }

                // 清理 90 天前的历史记录
                val ninetyDaysAgo = nowMillis - 90L * 24 * 3600 * 1000L
                repository.deleteOlderRecords(ninetyDaysAgo)
            } catch (e: Exception) {
                Log.e(TAG, "reconcile failed", e)
            }
        }
    }

    private suspend fun scheduleRuleForDate(
        rule: AutoChatRule,
        date: LocalDate,
        nowMillis: Long,
        workManager: WorkManager,
    ) {
        val dateStr = date.format(AutoChatTimeEngine.DATE_FORMATTER)

        // 1. 固定时间槽
        val fixedSlots = timeEngine.calculateFixedSlots(rule, date)
        for (slot in fixedSlots) {
            // 如果计划时间已经过去超过 maxDelayMinutes，且未排过，跳过
            if (timeEngine.isDelayedExpired(slot.scheduledTimeMillis, nowMillis, rule.maxDelayMinutes)) {
                continue
            }

            var record = repository.getOccurrence(rule.id, dateStr, slot.slotIndex, AutoChatType.FIXED)
            if (record == null) {
                val newRecord = AutoChatRecord(
                    id = Uuid.random(),
                    ruleId = rule.id,
                    assistantId = rule.assistantId,
                    conversationId = rule.conversationId,
                    occurrenceType = AutoChatType.FIXED,
                    targetDate = dateStr,
                    slotIndex = slot.slotIndex,
                    scheduledTime = slot.scheduledTimeMillis,
                    status = AutoChatStatus.SCHEDULED,
                )
                repository.insertRecord(newRecord)
                record = newRecord
            }

            if (record.status == AutoChatStatus.SCHEDULED) {
                enqueueWorker(record, workManager, nowMillis)
            }
        }

        // 2. 随机时间槽
        if (rule.randomEnabled) {
            val randomSlots = timeEngine.calculateRandomSlots(rule, date)
            for (slot in randomSlots) {
                if (timeEngine.isDelayedExpired(slot.scheduledTimeMillis, nowMillis, rule.maxDelayMinutes)) {
                    continue
                }

                var record = repository.getOccurrence(rule.id, dateStr, slot.slotIndex, AutoChatType.RANDOM)
                if (record == null) {
                    val newRecord = AutoChatRecord(
                        id = Uuid.random(),
                        ruleId = rule.id,
                        assistantId = rule.assistantId,
                        conversationId = rule.conversationId,
                        occurrenceType = AutoChatType.RANDOM,
                        targetDate = dateStr,
                        slotIndex = slot.slotIndex,
                        scheduledTime = slot.scheduledTimeMillis,
                        status = AutoChatStatus.SCHEDULED,
                    )
                    repository.insertRecord(newRecord)
                    record = newRecord
                }

                if (record.status == AutoChatStatus.SCHEDULED) {
                    enqueueWorker(record, workManager, nowMillis)
                }
            }
        }
    }

    private fun enqueueWorker(
        record: AutoChatRecord,
        workManager: WorkManager,
        nowMillis: Long,
    ) {
        val delayMillis = (record.scheduledTime - nowMillis).coerceAtLeast(0)
        val workName = "autochat_occ_${record.id}"

        val request = OneTimeWorkRequestBuilder<AutoChatWorker>()
            .setInitialDelay(delayMillis, TimeUnit.MILLISECONDS)
            .setInputData(AutoChatWorker.createInputData(record.id, record.ruleId))
            .addTag(TAG_AUTO_CHAT)
            .addTag("rule_${record.ruleId}")
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 5, TimeUnit.MINUTES)
            .build()

        workManager.enqueueUniqueWork(
            workName,
            ExistingWorkPolicy.KEEP,
            request
        )
    }

    /**
     * 取消规则并清理该规则下的未执行排期
     */
    suspend fun cancelRule(ruleId: Uuid): Unit = withContext(Dispatchers.IO) {
        mutex.withLock {
            try {
                val workManager = WorkManager.getInstance(context)
                workManager.cancelAllWorkByTag("rule_$ruleId")
                repository.deleteScheduledByRuleId(ruleId)
            } catch (e: Exception) {
                Log.e(TAG, "cancelRule failed: $ruleId", e)
            }
        }
    }

    /**
     * 备份恢复后的保护机制：
     * 取消旧的全部后台 WorkManager 任务，全部规则默认暂停并清理未执行任务，待用户确认开启
     */
    suspend fun onDatabaseRestored(): Unit = withContext(Dispatchers.IO) {
        mutex.withLock {
            try {
                val workManager = WorkManager.getInstance(context)
                workManager.cancelAllWorkByTag(TAG_AUTO_CHAT)
                repository.disableAllRules()
                repository.deleteAllScheduledRecords()
                Log.i(TAG, "Database restore handled: all auto-chat rules paused and work cancelled.")
            } catch (e: Exception) {
                Log.e(TAG, "onDatabaseRestored cleanup failed", e)
            }
        }
    }

    /**
     * 立即测试：走同一角色、会话、上下文和模型生成链路，独立测试标记，不计入限额与间隔
     */
    suspend fun triggerManualTest(rule: AutoChatRule): AutoChatExecutionResult = withContext(Dispatchers.IO) {
        val testRecord = AutoChatRecord(
            id = Uuid.random(),
            ruleId = rule.id,
            assistantId = rule.assistantId,
            conversationId = rule.conversationId,
            occurrenceType = AutoChatType.MANUAL_TEST,
            targetDate = timeEngine.getTodayDateString(),
            slotIndex = -1,
            scheduledTime = System.currentTimeMillis(),
            actualTime = System.currentTimeMillis(),
            status = AutoChatStatus.RUNNING,
        )
        repository.insertRecord(testRecord)
        lazyExecutionService.value.executeOccurrence(testRecord.id, isManualTest = true)
    }
}
