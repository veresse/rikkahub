package me.rerere.rikkahub.data.autochat

import me.rerere.ai.core.MessageRole
import me.rerere.ai.ui.UIMessage
import me.rerere.rikkahub.data.model.AutoChatRule
import java.time.Clock
import java.time.DayOfWeek
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlin.random.Random

data class ScheduledSlot(
    val slotIndex: Int,
    val scheduledTimeMillis: Long,
    val localDateTime: LocalDateTime,
)

data class TimeInterval(
    val start: LocalTime,
    val end: LocalTime,
) {
    val durationMinutes: Long
        get() {
            val s = start.toSecondOfDay()
            val e = end.toSecondOfDay()
            return if (e >= s) (e - s) / 60L else ((24 * 3600 - s) + e) / 60L
        }
}

class AutoChatTimeEngine(
    private val clock: Clock = Clock.systemDefaultZone(),
    private val zoneId: ZoneId = ZoneId.systemDefault(),
    private val random: Random = Random.Default,
) {
    companion object {
        val DATE_FORMATTER: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")

        fun parseLocalTime(str: String): LocalTime {
            val parts = str.trim().split(":")
            val hour = parts.getOrNull(0)?.toIntOrNull() ?: 0
            val minute = parts.getOrNull(1)?.toIntOrNull() ?: 0
            return LocalTime.of(hour.coerceIn(0, 23), minute.coerceIn(0, 59))
        }

        fun isTimeInQuietHours(
            time: LocalTime,
            quietHoursEnabled: Boolean,
            start: LocalTime,
            end: LocalTime,
        ): Boolean {
            if (!quietHoursEnabled) return false
            return if (start == end) {
                false
            } else if (start.isBefore(end)) {
                // 例如 01:00 ~ 06:00
                !time.isBefore(start) && time.isBefore(end)
            } else {
                // 跨午夜，例如 22:00 ~ 07:00
                !time.isBefore(start) || time.isBefore(end)
            }
        }
    }

    fun getTodayDate(): LocalDate = LocalDate.now(clock.withZone(zoneId))

    fun getTodayDateString(): String = getTodayDate().format(DATE_FORMATTER)

    fun nowMillis(): Long = clock.millis()

    fun calculateFixedSlots(
        rule: AutoChatRule,
        date: LocalDate = getTodayDate(),
    ): List<ScheduledSlot> {
        val distinctTimes = rule.fixedTimes
            .map { parseLocalTime(it) }
            .distinct()
            .sorted()

        return distinctTimes.mapIndexed { index, time ->
            val ldt = LocalDateTime.of(date, time)
            val zdt = ldt.atZone(zoneId)
            ScheduledSlot(
                slotIndex = index,
                scheduledTimeMillis = zdt.toInstant().toEpochMilli(),
                localDateTime = ldt,
            )
        }
    }

    /**
     * 计算随机窗口内的有效时间区间（剔除安静时段）。
     * [randomWindowStart, randomWindowEnd)
     */
    fun calculateAvailableRandomIntervals(
        windowStart: LocalTime,
        windowEnd: LocalTime,
        quietEnabled: Boolean,
        quietStart: LocalTime,
        quietEnd: LocalTime,
    ): List<TimeInterval> {
        val startSec = windowStart.toSecondOfDay()
        val endSec = windowEnd.toSecondOfDay()

        // 窗口必须合法：startSec < endSec（同一天内），若相等或反序则无效
        if (startSec >= endSec) return emptyList()

        if (!quietEnabled || quietStart == quietEnd) {
            return listOf(TimeInterval(windowStart, windowEnd))
        }

        val quietCrossMidnight = quietStart.isAfter(quietEnd)

        // 逐分钟划分或按边界拆分，收集所有不在安静时段的区间
        val availableMinutes = mutableListOf<Int>()
        for (sec in startSec until endSec step 60) {
            val t = LocalTime.ofSecondOfDay(sec.toLong())
            val inQuiet = if (quietCrossMidnight) {
                !t.isBefore(quietStart) || t.isBefore(quietEnd)
            } else {
                !t.isBefore(quietStart) && t.isBefore(quietEnd)
            }
            if (!inQuiet) {
                availableMinutes.add(sec)
            }
        }

        if (availableMinutes.isEmpty()) return emptyList()

        // 将连续分钟拼接成 TimeInterval
        val intervals = mutableListOf<TimeInterval>()
        var curStartSec = availableMinutes.first()
        var curPrevSec = curStartSec

        for (i in 1 until availableMinutes.size) {
            val sec = availableMinutes[i]
            if (sec == curPrevSec + 60) {
                curPrevSec = sec
            } else {
                intervals.add(
                    TimeInterval(
                        LocalTime.ofSecondOfDay(curStartSec.toLong()),
                        LocalTime.ofSecondOfDay((curPrevSec + 60).toLong().coerceAtMost(86399))
                    )
                )
                curStartSec = sec
                curPrevSec = sec
            }
        }
        intervals.add(
            TimeInterval(
                LocalTime.ofSecondOfDay(curStartSec.toLong()),
                LocalTime.ofSecondOfDay((curPrevSec + 60).toLong().coerceAtMost(86399))
            )
        )
        return intervals
    }

    /**
     * 为某一天安排随机时间点。
     * 满足：
     * 1. 当天是规则配置的 randomDaysOfWeek；
     * 2. 时间落在有效区间内；
     * 3. 安排的数量最多为 randomCountPerDay；
     * 4. 多个时间点之间满足 minIntervalMinutes。
     */
    fun calculateRandomSlots(
        rule: AutoChatRule,
        date: LocalDate = getTodayDate(),
    ): List<ScheduledSlot> {
        if (!rule.randomEnabled) return emptyList()

        val dayOfWeekValue = date.dayOfWeek.value // 1..7
        if (dayOfWeekValue !in rule.randomDaysOfWeek) return emptyList()

        val windowStart = parseLocalTime(rule.randomWindowStart)
        val windowEnd = parseLocalTime(rule.randomWindowEnd)
        val quietStart = parseLocalTime(rule.quietHoursStart)
        val quietEnd = parseLocalTime(rule.quietHoursEnd)

        val intervals = calculateAvailableRandomIntervals(
            windowStart = windowStart,
            windowEnd = windowEnd,
            quietEnabled = rule.quietHoursEnabled,
            quietStart = quietStart,
            quietEnd = quietEnd,
        )

        if (intervals.isEmpty()) return emptyList()

        val count = rule.randomCountPerDay.coerceIn(1, 3)
        val minIntervalSec = (rule.minIntervalMinutes * 60).coerceAtLeast(0)

        // 把所有可用的秒收集起来
        val validSeconds = mutableListOf<Int>()
        for (interval in intervals) {
            val s = interval.start.toSecondOfDay()
            val e = interval.end.toSecondOfDay()
            for (sec in s until e) {
                validSeconds.add(sec)
            }
        }

        if (validSeconds.isEmpty()) return emptyList()

        // 抽样算法：
        // 如果 count == 1，直接在 validSeconds 中均匀随机抽取一个
        val sampledSeconds = mutableListOf<Int>()
        if (count == 1) {
            val picked = validSeconds[random.nextInt(validSeconds.size)]
            sampledSeconds.add(picked)
        } else {
            // 将有效秒切分成 count 个大致等分的分区，在各分区内采样，并检验两两间距 >= minIntervalSec
            val partitionSize = validSeconds.size / count
            for (i in 0 until count) {
                val startIdx = i * partitionSize
                val endIdx = if (i == count - 1) validSeconds.size else (i + 1) * partitionSize
                if (startIdx < endIdx) {
                    val pickIdx = random.nextInt(startIdx, endIdx)
                    val pickSec = validSeconds[pickIdx]
                    val lastSec = sampledSeconds.lastOrNull()
                    if (lastSec == null || pickSec - lastSec >= minIntervalSec) {
                        sampledSeconds.add(pickSec)
                    }
                }
            }
            // 如果因为间距要求过滤掉了一些，确保至少有第一个
            if (sampledSeconds.isEmpty()) {
                sampledSeconds.add(validSeconds[random.nextInt(validSeconds.size)])
            }
        }

        sampledSeconds.sort()

        return sampledSeconds.mapIndexed { index, sec ->
            val time = LocalTime.ofSecondOfDay(sec.toLong())
            val ldt = LocalDateTime.of(date, time)
            val zdt = ldt.atZone(zoneId)
            ScheduledSlot(
                slotIndex = index,
                scheduledTimeMillis = zdt.toInstant().toEpochMilli(),
                localDateTime = ldt,
            )
        }
    }

    /**
     * 判断是否延迟超时（missed）
     */
    fun isDelayedExpired(
        scheduledTimeMillis: Long,
        currentTimeMillis: Long = nowMillis(),
        maxDelayMinutes: Int = 60,
    ): Boolean {
        if (currentTimeMillis <= scheduledTimeMillis) return false
        val delayMillis = currentTimeMillis - scheduledTimeMillis
        return delayMillis > maxDelayMinutes * 60 * 1000L
    }

    /**
     * 检查当前随机任务是否允许执行（考虑当天上限与尚未执行的固定问候预留）
     */
    fun canExecuteRandomTask(
        currentSucceededToday: Int,
        dailyMaxMessages: Int,
        remainingFixedSlotsToday: Int,
    ): Boolean {
        if (currentSucceededToday >= dailyMaxMessages) return false
        if (currentSucceededToday + remainingFixedSlotsToday >= dailyMaxMessages) return false
        return true
    }

    /**
     * 检查当前随机任务是否满足与上次成功消息的最短间隔
     */
    fun isMinIntervalSatisfied(
        lastSuccessMillis: Long?,
        currentTimeMillis: Long = nowMillis(),
        minIntervalMinutes: Int = 90,
    ): Boolean {
        if (lastSuccessMillis == null) return true
        val diff = currentTimeMillis - lastSuccessMillis
        return diff >= minIntervalMinutes * 60 * 1000L
    }

    /**
     * 未读防打扰检查：
     * 传入自上一次成功主动消息之后的所有历史节点；
     * 若包含真实有效的用户消息（非 synthetic、非纯 notice），则返回 true（表示用户已回复）；
     * 若没有包含任何真实用户消息，则返回 false（表示用户未读/未回复）。
     */
    fun hasUserRepliedSinceLastAuto(
        messagesSinceLastAuto: List<UIMessage>
    ): Boolean {
        return messagesSinceLastAuto.any { msg ->
            msg.role == MessageRole.USER && !msg.isSynthetic && !msg.isNoticeOnly && msg.isValidToUpload()
        }
    }
}
