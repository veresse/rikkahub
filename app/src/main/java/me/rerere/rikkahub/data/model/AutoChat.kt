package me.rerere.rikkahub.data.model

import kotlinx.serialization.Serializable
import kotlin.uuid.Uuid

@Serializable
enum class AutoChatType {
    FIXED,
    RANDOM,
    MANUAL_TEST,
}

@Serializable
enum class AutoChatStatus {
    SCHEDULED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    SKIPPED,
    MISSED,
    CANCELLED,
    INTERRUPTED,
}

object AutoChatSkipReason {
    const val QUIET_HOURS = "QUIET_HOURS"
    const val DAILY_LIMIT_REACHED = "DAILY_LIMIT_REACHED"
    const val RESERVED_FOR_FIXED = "RESERVED_FOR_FIXED"
    const val MIN_INTERVAL_NOT_MET = "MIN_INTERVAL_NOT_MET"
    const val UNREAD_UNREPLIED = "UNREAD_UNREPLIED"
    const val WINDOW_EXPIRED = "WINDOW_EXPIRED"
    const val MISSED_DELAY = "MISSED_DELAY"
    const val SESSION_BUSY = "SESSION_BUSY"
    const val NO_VALID_TIME = "NO_VALID_TIME"
    const val RULE_DISABLED = "RULE_DISABLED"
    const val TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
}

object AutoChatPromptDefaults {
    const val DEFAULT_FIXED_PROMPT =
        "以你所扮演的角色身份向我发送一条早晨问候。结合我们之前的聊天背景与互动关系，自然流露情感，用词符合角色设定。避免模板化话术与机械问候，不知道的事情不要凭空编造，保持自然适中的对话长度。"

    const val DEFAULT_RANDOM_PROMPT =
        "以你所扮演的角色身份主动向我发起一段自然的简短对话。可以延续我们近期聊过的话题，或者分享一些符合你角色日常生活的琐事与想法。不要假装知晓我现在具体在做什么或心情如何，也不强求我立即回复。"
}

@Serializable
data class AutoChatRule(
    val id: Uuid = Uuid.random(),
    val name: String = "",
    val assistantId: Uuid = Uuid.random(),
    val conversationId: Uuid = Uuid.random(),
    val enabled: Boolean = false,
    val fixedTimes: List<String> = listOf("08:00"),
    val randomEnabled: Boolean = true,
    val randomDaysOfWeek: Set<Int> = setOf(1, 2, 3, 4, 5), // 1 = Monday ... 7 = Sunday
    val randomWindowStart: String = "09:00",
    val randomWindowEnd: String = "18:00",
    val randomCountPerDay: Int = 1,
    val minIntervalMinutes: Int = 90,
    val quietHoursEnabled: Boolean = true,
    val quietHoursStart: String = "22:00",
    val quietHoursEnd: String = "07:00",
    val dailyMaxMessages: Int = 2,
    val skipIfUnread: Boolean = false,
    val fixedPrompt: String = AutoChatPromptDefaults.DEFAULT_FIXED_PROMPT,
    val randomPrompt: String = AutoChatPromptDefaults.DEFAULT_RANDOM_PROMPT,
    val maxDelayMinutes: Int = 60,
    val enableNotification: Boolean = true,
    val showPreview: Boolean = false,
    val version: Long = 1L,
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)

@Serializable
data class AutoChatRecord(
    val id: Uuid = Uuid.random(),
    val ruleId: Uuid,
    val assistantId: Uuid,
    val conversationId: Uuid,
    val occurrenceType: AutoChatType,
    val targetDate: String, // yyyy-MM-dd
    val slotIndex: Int = 0,
    val scheduledTime: Long,
    val actualTime: Long? = null,
    val status: AutoChatStatus = AutoChatStatus.SCHEDULED,
    val messageId: Uuid? = null,
    val errorMessage: String? = null,
    val skipReason: String? = null,
    val contextSummary: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)
