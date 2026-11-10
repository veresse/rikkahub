package me.rerere.rikkahub.data.db.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.serialization.encodeToString
import me.rerere.rikkahub.data.model.AutoChatPromptDefaults
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import me.rerere.rikkahub.utils.JsonInstant
import kotlin.uuid.Uuid

@Entity(
    tableName = "auto_chat_rules",
    indices = [
        Index(value = ["conversation_id"]),
        Index(value = ["assistant_id"]),
    ]
)
data class AutoChatRuleEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "name")
    val name: String,

    @ColumnInfo(name = "assistant_id")
    val assistantId: String,

    @ColumnInfo(name = "conversation_id")
    val conversationId: String,

    @ColumnInfo(name = "enabled")
    val enabled: Boolean,

    @ColumnInfo(name = "fixed_times")
    val fixedTimes: String, // JSON array of "HH:mm"

    @ColumnInfo(name = "random_enabled")
    val randomEnabled: Boolean,

    @ColumnInfo(name = "random_days_of_week")
    val randomDaysOfWeek: String, // JSON array of Int (1..7)

    @ColumnInfo(name = "random_window_start")
    val randomWindowStart: String,

    @ColumnInfo(name = "random_window_end")
    val randomWindowEnd: String,

    @ColumnInfo(name = "random_count_per_day")
    val randomCountPerDay: Int,

    @ColumnInfo(name = "min_interval_minutes")
    val minIntervalMinutes: Int,

    @ColumnInfo(name = "quiet_hours_enabled")
    val quietHoursEnabled: Boolean,

    @ColumnInfo(name = "quiet_hours_start")
    val quietHoursStart: String,

    @ColumnInfo(name = "quiet_hours_end")
    val quietHoursEnd: String,

    @ColumnInfo(name = "daily_max_messages")
    val dailyMaxMessages: Int,

    @ColumnInfo(name = "skip_if_unread")
    val skipIfUnread: Boolean,

    @ColumnInfo(name = "fixed_prompt")
    val fixedPrompt: String,

    @ColumnInfo(name = "random_prompt")
    val randomPrompt: String,

    @ColumnInfo(name = "max_delay_minutes")
    val maxDelayMinutes: Int,

    @ColumnInfo(name = "enable_notification")
    val enableNotification: Boolean,

    @ColumnInfo(name = "show_preview")
    val showPreview: Boolean,

    @ColumnInfo(name = "version")
    val version: Long,

    @ColumnInfo(name = "created_at")
    val createdAt: Long,

    @ColumnInfo(name = "updated_at")
    val updatedAt: Long,
) {
    fun toModel(): AutoChatRule = AutoChatRule(
        id = Uuid.parse(id),
        name = name,
        assistantId = Uuid.parse(assistantId),
        conversationId = Uuid.parse(conversationId),
        enabled = enabled,
        fixedTimes = runCatching { JsonInstant.decodeFromString<List<String>>(fixedTimes) }.getOrDefault(listOf("08:00")),
        randomEnabled = randomEnabled,
        randomDaysOfWeek = runCatching { JsonInstant.decodeFromString<Set<Int>>(randomDaysOfWeek) }.getOrDefault(setOf(1, 2, 3, 4, 5)),
        randomWindowStart = randomWindowStart,
        randomWindowEnd = randomWindowEnd,
        randomCountPerDay = randomCountPerDay,
        minIntervalMinutes = minIntervalMinutes,
        quietHoursEnabled = quietHoursEnabled,
        quietHoursStart = quietHoursStart,
        quietHoursEnd = quietHoursEnd,
        dailyMaxMessages = dailyMaxMessages,
        skipIfUnread = skipIfUnread,
        fixedPrompt = fixedPrompt.ifBlank { AutoChatPromptDefaults.DEFAULT_FIXED_PROMPT },
        randomPrompt = randomPrompt.ifBlank { AutoChatPromptDefaults.DEFAULT_RANDOM_PROMPT },
        maxDelayMinutes = maxDelayMinutes,
        enableNotification = enableNotification,
        showPreview = showPreview,
        version = version,
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

    companion object {
        fun fromModel(model: AutoChatRule): AutoChatRuleEntity = AutoChatRuleEntity(
            id = model.id.toString(),
            name = model.name,
            assistantId = model.assistantId.toString(),
            conversationId = model.conversationId.toString(),
            enabled = model.enabled,
            fixedTimes = JsonInstant.encodeToString(model.fixedTimes),
            randomEnabled = model.randomEnabled,
            randomDaysOfWeek = JsonInstant.encodeToString(model.randomDaysOfWeek),
            randomWindowStart = model.randomWindowStart,
            randomWindowEnd = model.randomWindowEnd,
            randomCountPerDay = model.randomCountPerDay,
            minIntervalMinutes = model.minIntervalMinutes,
            quietHoursEnabled = model.quietHoursEnabled,
            quietHoursStart = model.quietHoursStart,
            quietHoursEnd = model.quietHoursEnd,
            dailyMaxMessages = model.dailyMaxMessages,
            skipIfUnread = model.skipIfUnread,
            fixedPrompt = model.fixedPrompt,
            randomPrompt = model.randomPrompt,
            maxDelayMinutes = model.maxDelayMinutes,
            enableNotification = model.enableNotification,
            showPreview = model.showPreview,
            version = model.version,
            createdAt = model.createdAt,
            updatedAt = model.updatedAt,
        )
    }
}

@Entity(
    tableName = "auto_chat_records",
    foreignKeys = [
        ForeignKey(
            entity = AutoChatRuleEntity::class,
            parentColumns = ["id"],
            childColumns = ["rule_id"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [
        Index(value = ["rule_id"]),
        Index(value = ["conversation_id", "target_date"]),
        Index(value = ["rule_id", "target_date", "slot_index", "occurrence_type"], unique = true),
    ]
)
data class AutoChatRecordEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "rule_id")
    val ruleId: String,

    @ColumnInfo(name = "assistant_id")
    val assistantId: String,

    @ColumnInfo(name = "conversation_id")
    val conversationId: String,

    @ColumnInfo(name = "occurrence_type")
    val occurrenceType: String,

    @ColumnInfo(name = "target_date")
    val targetDate: String,

    @ColumnInfo(name = "slot_index")
    val slotIndex: Int,

    @ColumnInfo(name = "scheduled_time")
    val scheduledTime: Long,

    @ColumnInfo(name = "actual_time")
    val actualTime: Long?,

    @ColumnInfo(name = "status")
    val status: String,

    @ColumnInfo(name = "message_id")
    val messageId: String?,

    @ColumnInfo(name = "error_message")
    val errorMessage: String?,

    @ColumnInfo(name = "skip_reason")
    val skipReason: String?,

    @ColumnInfo(name = "context_summary")
    val contextSummary: String?,

    @ColumnInfo(name = "created_at")
    val createdAt: Long,
) {
    fun toModel(): AutoChatRecord = AutoChatRecord(
        id = Uuid.parse(id),
        ruleId = Uuid.parse(ruleId),
        assistantId = Uuid.parse(assistantId),
        conversationId = Uuid.parse(conversationId),
        occurrenceType = runCatching { AutoChatType.valueOf(occurrenceType) }.getOrDefault(AutoChatType.FIXED),
        targetDate = targetDate,
        slotIndex = slotIndex,
        scheduledTime = scheduledTime,
        actualTime = actualTime,
        status = runCatching { AutoChatStatus.valueOf(status) }.getOrDefault(AutoChatStatus.SCHEDULED),
        messageId = messageId?.let { runCatching { Uuid.parse(it) }.getOrNull() },
        errorMessage = errorMessage,
        skipReason = skipReason,
        contextSummary = contextSummary,
        createdAt = createdAt,
    )

    companion object {
        fun fromModel(model: AutoChatRecord): AutoChatRecordEntity = AutoChatRecordEntity(
            id = model.id.toString(),
            ruleId = model.ruleId.toString(),
            assistantId = model.assistantId.toString(),
            conversationId = model.conversationId.toString(),
            occurrenceType = model.occurrenceType.name,
            targetDate = model.targetDate,
            slotIndex = model.slotIndex,
            scheduledTime = model.scheduledTime,
            actualTime = model.actualTime,
            status = model.status.name,
            messageId = model.messageId?.toString(),
            errorMessage = model.errorMessage,
            skipReason = model.skipReason,
            contextSummary = model.contextSummary,
            createdAt = model.createdAt,
        )
    }
}
