package me.rerere.rikkahub.data.autochat

import me.rerere.ai.core.MessageRole
import me.rerere.ai.ui.UIMessage
import me.rerere.ai.ui.UIMessagePart
import me.rerere.rikkahub.data.model.AutoChatRule
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import kotlin.random.Random

class AutoChatTimeEngineTest {

    private val fixedZone = ZoneId.of("Asia/Shanghai")
    // 2026-11-09 是周一 (Monday = 1)
    private val mondayInstant = Instant.parse("2026-11-09T00:00:00Z")
    private val mondayClock = Clock.fixed(mondayInstant, fixedZone)

    @Test
    fun `default fixed schedule produces 08_00 slot`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val rule = AutoChatRule(fixedTimes = listOf("08:00"))

        val slots = engine.calculateFixedSlots(rule, LocalDate.of(2026, 11, 9))
        assertEquals(1, slots.size)
        assertEquals(LocalTime.of(8, 0), slots[0].localDateTime.toLocalTime())
        assertEquals(0, slots[0].slotIndex)
    }

    @Test
    fun `multiple fixed times deduplicate and sort`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val rule = AutoChatRule(fixedTimes = listOf("14:30", "08:00", "08:00", "12:00"))

        val slots = engine.calculateFixedSlots(rule, LocalDate.of(2026, 11, 9))
        assertEquals(3, slots.size)
        assertEquals(LocalTime.of(8, 0), slots[0].localDateTime.toLocalTime())
        assertEquals(LocalTime.of(12, 0), slots[1].localDateTime.toLocalTime())
        assertEquals(LocalTime.of(14, 30), slots[2].localDateTime.toLocalTime())
    }

    @Test
    fun `random schedule runs on configured weekdays and skips weekend`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val rule = AutoChatRule(
            randomEnabled = true,
            randomDaysOfWeek = setOf(1, 2, 3, 4, 5), // Mon..Fri
            randomWindowStart = "09:00",
            randomWindowEnd = "18:00",
            randomCountPerDay = 1,
        )

        // 2026-11-09 是周一 -> 应该安排
        val mondaySlots = engine.calculateRandomSlots(rule, LocalDate.of(2026, 11, 9))
        assertEquals(1, mondaySlots.size)
        val pickedTime = mondaySlots[0].localDateTime.toLocalTime()
        assertTrue(pickedTime >= LocalTime.of(9, 0))
        assertTrue(pickedTime < LocalTime.of(18, 0))

        // 2026-11-15 是周日 -> 跳过
        val sundaySlots = engine.calculateRandomSlots(rule, LocalDate.of(2026, 11, 15))
        assertTrue(sundaySlots.isEmpty())
    }

    @Test
    fun `quiet hours cross midnight correctly excludes overlapping window`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        // 安静时段 22:00 ~ 07:00
        val quietStart = LocalTime.of(22, 0)
        val quietEnd = LocalTime.of(7, 0)

        // 窗口 06:00 ~ 12:00 与安静时段重叠 06:00~07:00，有效可用应为 07:00 ~ 12:00
        val intervals = engine.calculateAvailableRandomIntervals(
            windowStart = LocalTime.of(6, 0),
            windowEnd = LocalTime.of(12, 0),
            quietEnabled = true,
            quietStart = quietStart,
            quietEnd = quietEnd,
        )

        assertEquals(1, intervals.size)
        assertEquals(LocalTime.of(7, 0), intervals[0].start)
        assertEquals(LocalTime.of(12, 0), intervals[0].end)
    }

    @Test
    fun `random window completely inside quiet hours returns empty`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        // 安静时段 22:00 ~ 07:00，窗口 01:00 ~ 05:00
        val intervals = engine.calculateAvailableRandomIntervals(
            windowStart = LocalTime.of(1, 0),
            windowEnd = LocalTime.of(5, 0),
            quietEnabled = true,
            quietStart = LocalTime.of(22, 0),
            quietEnd = LocalTime.of(7, 0),
        )
        assertTrue(intervals.isEmpty())
    }

    @Test
    fun `quiet hours check supports daytime and cross midnight`() {
        val qStart = LocalTime.of(22, 0)
        val qEnd = LocalTime.of(7, 0)

        // 23:00 在安静时段
        assertTrue(AutoChatTimeEngine.isTimeInQuietHours(LocalTime.of(23, 0), true, qStart, qEnd))
        // 05:30 在安静时段
        assertTrue(AutoChatTimeEngine.isTimeInQuietHours(LocalTime.of(5, 30), true, qStart, qEnd))
        // 08:00 不在安静时段
        assertFalse(AutoChatTimeEngine.isTimeInQuietHours(LocalTime.of(8, 0), true, qStart, qEnd))
        // 21:59 不在安静时段
        assertFalse(AutoChatTimeEngine.isTimeInQuietHours(LocalTime.of(21, 59), true, qStart, qEnd))
    }

    @Test
    fun `delay expiration correctly checks maxDelayMinutes`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val scheduled = 1000000L
        val maxDelay = 60

        // 延迟 30 分钟 -> 未过期
        val now30 = scheduled + 30 * 60 * 1000L
        assertFalse(engine.isDelayedExpired(scheduled, now30, maxDelay))

        // 延迟 61 分钟 -> 已超时 (missed)
        val now61 = scheduled + 61 * 60 * 1000L
        assertTrue(engine.isDelayedExpired(scheduled, now61, maxDelay))
    }

    @Test
    fun `min interval between random messages enforced`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val lastSuccess = 1000000L
        val minInterval = 90 // 分钟

        // 差 60 分钟 -> 不满足
        assertFalse(engine.isMinIntervalSatisfied(lastSuccess, lastSuccess + 60 * 60 * 1000L, minInterval))
        // 差 90 分钟 -> 满足
        assertTrue(engine.isMinIntervalSatisfied(lastSuccess, lastSuccess + 90 * 60 * 1000L, minInterval))
        // 差 120 分钟 -> 满足
        assertTrue(engine.isMinIntervalSatisfied(lastSuccess, lastSuccess + 120 * 60 * 1000L, minInterval))
        // 没有上一条 -> 满足
        assertTrue(engine.isMinIntervalSatisfied(null, lastSuccess, minInterval))
    }

    @Test
    fun `random task respects daily limit and reserves slots for remaining fixed greetings`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)
        val dailyMax = 2

        // 已经成功了 2 条 -> 达到上限，不能执行随机
        assertFalse(engine.canExecuteRandomTask(currentSucceededToday = 2, dailyMaxMessages = dailyMax, remainingFixedSlotsToday = 0))

        // 已经成功了 1 条，但今天还有 1 个未到达的固定问候 -> 剩余名额预留给固定问候，不能执行随机！
        assertFalse(engine.canExecuteRandomTask(currentSucceededToday = 1, dailyMaxMessages = dailyMax, remainingFixedSlotsToday = 1))

        // 已经成功了 1 条，今天固定问候已全部完成 (remainingFixed = 0) -> 可以执行随机
        assertTrue(engine.canExecuteRandomTask(currentSucceededToday = 1, dailyMaxMessages = dailyMax, remainingFixedSlotsToday = 0))

        // 成功 0 条，今天有 1 个固定问候 -> 2 - 1 = 1 个名额给随机，可以执行
        assertTrue(engine.canExecuteRandomTask(currentSucceededToday = 0, dailyMaxMessages = dailyMax, remainingFixedSlotsToday = 1))
    }

    @Test
    fun `unreplied check detects user message accurately`() {
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone)

        // 场景 A：只有助手的消息 -> 用户未回复
        val messagesOnlyAssistant = listOf(
            UIMessage.assistant("早安！"),
        )
        assertFalse(engine.hasUserRepliedSinceLastAuto(messagesOnlyAssistant))

        // 场景 B：含有 synthetic 或纯 notice 消息 -> 仍属于系统产生，不计为真实用户回复
        val messagesWithNotice = listOf(
            UIMessage.user("notice").copy(isSynthetic = true),
            UIMessage(role = MessageRole.USER, parts = listOf(UIMessagePart.Notice("title", "content"))),
        )
        assertFalse(engine.hasUserRepliedSinceLastAuto(messagesWithNotice))

        // 场景 C：用户回复了真实文字 -> 判定为已回复
        val messagesWithRealUser = listOf(
            UIMessage.assistant("早安！"),
            UIMessage.user("早呀！今天天气不错"),
        )
        assertTrue(engine.hasUserRepliedSinceLastAuto(messagesWithRealUser))
    }

    @Test
    fun `multiple random slots in single day respect count and minimum interval`() {
        val seededRandom = Random(42)
        val engine = AutoChatTimeEngine(clock = mondayClock, zoneId = fixedZone, random = seededRandom)
        val rule = AutoChatRule(
            randomEnabled = true,
            randomDaysOfWeek = setOf(1),
            randomWindowStart = "09:00",
            randomWindowEnd = "18:00",
            randomCountPerDay = 2,
            minIntervalMinutes = 120, // 2小时
        )

        val slots = engine.calculateRandomSlots(rule, LocalDate.of(2026, 11, 9))
        assertTrue(slots.size in 1..2)
        if (slots.size == 2) {
            val diffSec = slots[1].scheduledTimeMillis - slots[0].scheduledTimeMillis
            assertTrue(diffSec >= 120 * 60 * 1000L)
        }
    }
}
