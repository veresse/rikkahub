package me.rerere.rikkahub.service.autochat

import android.app.PendingIntent
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import me.rerere.ai.agent.AgentEvent
import me.rerere.ai.agent.runAgent
import me.rerere.ai.core.MessageRole
import me.rerere.ai.ui.ModelSnapshot
import me.rerere.ai.ui.UIMessage
import me.rerere.ai.ui.UIMessageAnnotation
import me.rerere.ai.ui.UIMessagePart
import me.rerere.rikkahub.CHAT_COMPLETED_NOTIFICATION_CHANNEL_ID
import me.rerere.rikkahub.R
import me.rerere.rikkahub.conversationIntent
import me.rerere.rikkahub.data.ai.ChatRequestFactory
import me.rerere.rikkahub.data.autochat.AutoChatTimeEngine
import me.rerere.rikkahub.data.datastore.SettingsStore
import me.rerere.rikkahub.data.datastore.findModelById
import me.rerere.rikkahub.data.datastore.findProvider
import me.rerere.rikkahub.data.datastore.getAssistantById
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.AutoChatSkipReason
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import me.rerere.rikkahub.data.model.getAssistantOf
import me.rerere.rikkahub.data.model.getChatModelOf
import me.rerere.rikkahub.data.model.toMessageNode
import me.rerere.rikkahub.data.repository.AutoChatRepository
import me.rerere.rikkahub.data.repository.ConversationRepository
import me.rerere.rikkahub.service.ChatService
import me.rerere.rikkahub.utils.NotificationUtil
import me.rerere.rikkahub.utils.sendNotification
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.uuid.Uuid

private const val TAG = "AutoChatExecution"

sealed class AutoChatExecutionResult {
    data class Success(val messageId: Uuid, val text: String) : AutoChatExecutionResult()
    data class Skipped(val reason: String) : AutoChatExecutionResult()
    data class Failed(val error: Throwable) : AutoChatExecutionResult()
}

class AutoChatExecutionService(
    private val context: Context,
    private val repository: AutoChatRepository,
    private val settingsStore: SettingsStore,
    private val chatService: ChatService,
    private val conversationRepo: ConversationRepository,
    private val requestFactory: ChatRequestFactory,
    private val timeEngine: AutoChatTimeEngine = AutoChatTimeEngine(),
) {
    suspend fun executeOccurrence(
        occurrenceId: Uuid,
        isManualTest: Boolean = false
    ): AutoChatExecutionResult = withContext(Dispatchers.IO) {
        val record = repository.getRecordById(occurrenceId)
            ?: return@withContext AutoChatExecutionResult.Failed(IllegalArgumentException("Occurrence not found: $occurrenceId"))

        // 原子 Claim，防止重复执行
        if (!isManualTest) {
            val claimed = repository.claimOccurrence(
                id = record.id,
                fromStatus = AutoChatStatus.SCHEDULED,
                toStatus = AutoChatStatus.RUNNING,
                actualTime = System.currentTimeMillis()
            )
            if (!claimed) {
                Log.w(TAG, "Occurrence ${record.id} already claimed or processed, skipping")
                return@withContext AutoChatExecutionResult.Skipped("ALREADY_CLAIMED")
            }
        }

        val rule = repository.getRuleById(record.ruleId)
        if (rule == null || (!rule.enabled && !isManualTest)) {
            repository.updateRecord(
                record.copy(
                    status = AutoChatStatus.CANCELLED,
                    actualTime = System.currentTimeMillis(),
                    skipReason = AutoChatSkipReason.RULE_DISABLED,
                    errorMessage = "规则不存在或已关闭",
                )
            )
            return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.RULE_DISABLED)
        }

        val settings = settingsStore.awaitLoaded()

        // 验证角色和会话是否存在且匹配
        val assistant = settings.getAssistantById(rule.assistantId)
        val conversation = conversationRepo.getConversationById(rule.conversationId)
        if (assistant == null || conversation == null || conversation.assistantId != rule.assistantId) {
            val reason = "绑定角色或会话已被删除或不匹配"
            repository.updateRecord(
                record.copy(
                    status = AutoChatStatus.FAILED,
                    actualTime = System.currentTimeMillis(),
                    skipReason = AutoChatSkipReason.TARGET_NOT_FOUND,
                    errorMessage = reason,
                )
            )
            // 自动暂停规则并提示重新绑定，绝不降级
            repository.updateRule(rule.copy(enabled = false))
            return@withContext AutoChatExecutionResult.Failed(IllegalStateException(reason))
        }

        // 验证模型配置
        val model = settings.getChatModelOf(conversation)
        if (model == null || model.findProvider(settings.providers) == null) {
            val reason = "模型配置缺失或供应商失效"
            repository.updateRecord(
                record.copy(
                    status = AutoChatStatus.FAILED,
                    actualTime = System.currentTimeMillis(),
                    errorMessage = reason,
                )
            )
            return@withContext AutoChatExecutionResult.Failed(IllegalStateException(reason))
        }

        // 非手动测试时，进行运行时各种条件检查
        if (!isManualTest) {
            val nowMillis = System.currentTimeMillis()

            // 1. 延迟超时检查
            if (timeEngine.isDelayedExpired(record.scheduledTime, nowMillis, rule.maxDelayMinutes)) {
                repository.updateRecord(
                    record.copy(
                        status = AutoChatStatus.MISSED,
                        actualTime = nowMillis,
                        skipReason = AutoChatSkipReason.MISSED_DELAY,
                        errorMessage = "任务触发延迟已超过设定的最大延迟限制（${rule.maxDelayMinutes}分钟）",
                    )
                )
                return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.MISSED_DELAY)
            }

            // 2. 安静时段检查
            val nowTime = java.time.LocalTime.now()
            val quietStart = AutoChatTimeEngine.parseLocalTime(rule.quietHoursStart)
            val quietEnd = AutoChatTimeEngine.parseLocalTime(rule.quietHoursEnd)
            if (AutoChatTimeEngine.isTimeInQuietHours(nowTime, rule.quietHoursEnabled, quietStart, quietEnd)) {
                repository.updateRecord(
                    record.copy(
                        status = AutoChatStatus.SKIPPED,
                        actualTime = nowMillis,
                        skipReason = AutoChatSkipReason.QUIET_HOURS,
                        errorMessage = "当前处于安静时段（${rule.quietHoursStart} - ${rule.quietHoursEnd}）",
                    )
                )
                return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.QUIET_HOURS)
            }

            // 3. 当天上限检查
            val todaySucceeded = repository.getSuccessfulCountOnDate(rule.conversationId, record.targetDate)
            if (todaySucceeded >= rule.dailyMaxMessages) {
                repository.updateRecord(
                    record.copy(
                        status = AutoChatStatus.SKIPPED,
                        actualTime = nowMillis,
                        skipReason = AutoChatSkipReason.DAILY_LIMIT_REACHED,
                        errorMessage = "已达到当天自动消息上限（${rule.dailyMaxMessages}条）",
                    )
                )
                return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.DAILY_LIMIT_REACHED)
            }

            // 4. 若为随机任务，检查为未到达的固定问候预留名额
            if (record.occurrenceType == AutoChatType.RANDOM) {
                val fixedSlots = timeEngine.calculateFixedSlots(rule)
                val remainingFixed = fixedSlots.count { it.scheduledTimeMillis > nowMillis }
                if (!timeEngine.canExecuteRandomTask(todaySucceeded, rule.dailyMaxMessages, remainingFixed)) {
                    repository.updateRecord(
                        record.copy(
                            status = AutoChatStatus.SKIPPED,
                            actualTime = nowMillis,
                            skipReason = AutoChatSkipReason.RESERVED_FOR_FIXED,
                            errorMessage = "剩余额度需预留给尚未到达的固定问候任务",
                        )
                    )
                    return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.RESERVED_FOR_FIXED)
                }

                // 5. 最短间隔检查
                val lastSuccess = repository.getLastSuccessfulRecord(rule.conversationId)
                val lastSuccessTime = lastSuccess?.actualTime ?: lastSuccess?.scheduledTime
                if (!timeEngine.isMinIntervalSatisfied(lastSuccessTime, nowMillis, rule.minIntervalMinutes)) {
                    repository.updateRecord(
                        record.copy(
                            status = AutoChatStatus.SKIPPED,
                            actualTime = nowMillis,
                            skipReason = AutoChatSkipReason.MIN_INTERVAL_NOT_MET,
                            errorMessage = "距上一条成功主动消息未达到最短间隔（${rule.minIntervalMinutes}分钟）",
                        )
                    )
                    return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.MIN_INTERVAL_NOT_MET)
                }

                // 6. 未读防打扰检查
                if (rule.skipIfUnread) {
                    val lastMessageId = lastSuccess?.messageId
                    val currentMessages = conversation.currentMessages
                    val messagesAfter = if (lastMessageId != null) {
                        val idx = currentMessages.indexOfFirst { it.id == lastMessageId }
                        if (idx != -1) currentMessages.drop(idx + 1) else currentMessages
                    } else {
                        currentMessages
                    }
                    if (!timeEngine.hasUserRepliedSinceLastAuto(messagesAfter)) {
                        repository.updateRecord(
                            record.copy(
                                status = AutoChatStatus.SKIPPED,
                                actualTime = nowMillis,
                                skipReason = AutoChatSkipReason.UNREAD_UNREPLIED,
                                errorMessage = "检测到上一条主动消息用户尚未回复，触发防打扰跳过",
                            )
                        )
                        return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.UNREAD_UNREPLIED)
                    }
                }
            }
        }

        // 会话互斥检查：真实用户消息优先，绝不打断正在进行的聊天生成
        if (chatService.isSessionBusy(rule.conversationId)) {
            val reason = "会话正在进行生成或等待审批，为避免打扰用户已跳过本次主动消息"
            repository.updateRecord(
                record.copy(
                    status = if (isManualTest) AutoChatStatus.FAILED else AutoChatStatus.SKIPPED,
                    actualTime = System.currentTimeMillis(),
                    skipReason = AutoChatSkipReason.SESSION_BUSY,
                    errorMessage = reason,
                )
            )
            return@withContext AutoChatExecutionResult.Skipped(AutoChatSkipReason.SESSION_BUSY)
        }

        // 开始实际生成
        try {
            var generatedMessageId: Uuid? = null
            var generatedTextResult = ""

            chatService.withExistingSession(rule.conversationId) { session ->
                val currentConversation = session.state.value
                val currentHistory = currentConversation.currentMessages

                // 格式化时间与提示词
                val nowStr = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))
                val triggerTypeStr = if (record.occurrenceType == AutoChatType.FIXED) "定时问候" else "日常主动对话"
                val basePrompt = if (record.occurrenceType == AutoChatType.FIXED) rule.fixedPrompt else rule.randomPrompt
                val promptWithTime = "【系统主动触发 - 当前时间: $nowStr, 类型: $triggerTypeStr】\n$basePrompt"

                val syntheticPrompt = UIMessage.user(promptWithTime).copy(isSynthetic = true)
                val messagesForModel = currentHistory + syntheticPrompt

                // 使用无副作用工具的上下文
                val turn = requestFactory.createAutoChatContext(settings, currentConversation)

                val (agentRequest, agentHooks) = requestFactory.createAgent(
                    turn = turn,
                    messages = messagesForModel,
                    processingStatus = MutableStateFlow(null),
                )

                var finalMessage: UIMessage? = null
                runAgent(agentRequest, agentHooks).flowOn(Dispatchers.IO).collect { event ->
                    if (event is AgentEvent.MessageUpdated) {
                        finalMessage = event.message
                    }
                }

                val text = finalMessage?.toText()?.trim().orEmpty()
                if (text.isBlank()) {
                    throw IllegalStateException("模型未返回有效文本内容")
                }

                // 独立创建助手节点，确保即使历史最后一条是助手也不会覆盖
                val newMessageId = Uuid.random()
                val autoChatMessage = (finalMessage ?: UIMessage.assistant(text)).copy(
                    id = newMessageId,
                    role = MessageRole.ASSISTANT,
                    parts = listOf(UIMessagePart.Text(text)),
                    annotations = listOf(UIMessageAnnotation.AutoChat(isTest = isManualTest)),
                    modelId = turn.model.id,
                    modelSnapshot = ModelSnapshot(modelId = turn.model.modelId, displayName = turn.model.displayName),
                    isSynthetic = false,
                )

                session.update { latest ->
                    latest.copy(
                        messageNodes = latest.messageNodes + autoChatMessage.toMessageNode()
                    )
                }
                session.flush()

                generatedMessageId = newMessageId
                generatedTextResult = text

                val contextCount = currentHistory.size
                val memoryUsed = turn.assistant.enableMemory && turn.memories.isNotEmpty()
                val contextSummary = "历史消息条数: $contextCount, 角色记忆生效: $memoryUsed, 模型: ${turn.model.displayName}"

                repository.updateRecord(
                    record.copy(
                        status = AutoChatStatus.SUCCEEDED,
                        actualTime = System.currentTimeMillis(),
                        messageId = newMessageId,
                        contextSummary = contextSummary,
                        errorMessage = null,
                        skipReason = null,
                    )
                )

                // 若开启通知且非测试模式，发送系统通知
                if (rule.enableNotification && !isManualTest) {
                    sendAutoChatNotification(
                        conversationId = rule.conversationId,
                        assistantName = assistant.name.ifEmpty { context.getString(R.string.assistant_page_default_assistant) },
                        content = if (rule.showPreview) text else "给你发来了一条消息",
                    )
                }
            }

            return@withContext AutoChatExecutionResult.Success(
                messageId = checkNotNull(generatedMessageId),
                text = generatedTextResult
            )
        } catch (e: Throwable) {
            Log.e(TAG, "AutoChat generation failed", e)
            repository.updateRecord(
                record.copy(
                    status = AutoChatStatus.FAILED,
                    actualTime = System.currentTimeMillis(),
                    errorMessage = e.message ?: e.javaClass.simpleName,
                )
            )
            return@withContext AutoChatExecutionResult.Failed(e)
        }
    }

    private fun sendAutoChatNotification(
        conversationId: Uuid,
        assistantName: String,
        content: String,
    ) {
        if (!NotificationUtil.hasNotificationPermission(context)) return

        val notificationId = ("autochat_$conversationId").hashCode()
        val pendingIntent = PendingIntent.getActivity(
            context,
            notificationId,
            conversationIntent(context, conversationId.toString()),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        context.sendNotification(
            channelId = CHAT_COMPLETED_NOTIFICATION_CHANNEL_ID,
            notificationId = notificationId
        ) {
            title = assistantName
            this.content = content
            autoCancel = true
            useDefaults = true
            category = NotificationCompat.CATEGORY_MESSAGE
            visibility = NotificationCompat.VISIBILITY_PRIVATE
            contentIntent = pendingIntent
        }
    }
}
