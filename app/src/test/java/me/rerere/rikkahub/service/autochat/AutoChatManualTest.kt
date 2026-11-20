package me.rerere.rikkahub.service.autochat

import kotlinx.coroutines.runBlocking
import me.rerere.rikkahub.data.db.dao.AutoChatRecordDAO
import me.rerere.rikkahub.data.db.dao.AutoChatRuleDAO
import me.rerere.rikkahub.data.db.entity.AutoChatRecordEntity
import me.rerere.rikkahub.data.db.entity.AutoChatRuleEntity
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.repository.AutoChatRepository
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.lang.reflect.Proxy

class AutoChatManualTest {
    private val rules = mutableMapOf<String, AutoChatRuleEntity>()
    private val records = mutableListOf<AutoChatRecordEntity>()
    private var rejectInsert = false

    private val ruleDAO = object : AutoChatRuleDAO by unusedDAO() {
        override suspend fun insertIfAbsent(rule: AutoChatRuleEntity): Long {
            if (rules.containsKey(rule.id)) return -1
            rules[rule.id] = rule
            return rules.size.toLong()
        }
    }

    private val recordDAO = object : AutoChatRecordDAO by unusedDAO() {
        override suspend fun getMinimumManualSlot(ruleId: String, targetDate: String): Int? =
            records.filter { it.ruleId == ruleId && it.targetDate == targetDate && it.occurrenceType == "MANUAL_TEST" }
                .minOfOrNull { it.slotIndex }

        override suspend fun insert(record: AutoChatRecordEntity): Long {
            check(rules.containsKey(record.ruleId)) { "Missing parent rule" }
            if (rejectInsert || records.any {
                    it.id == record.id || (it.ruleId == record.ruleId && it.targetDate == record.targetDate &&
                        it.slotIndex == record.slotIndex && it.occurrenceType == record.occurrenceType)
                }) return -1
            records += record
            return records.size.toLong()
        }

        // Run the production transaction body against the fake constraints.
        override suspend fun insertManualRecord(record: AutoChatRecordEntity): AutoChatRecordEntity =
            super<AutoChatRecordDAO>.insertManualRecord(record)
    }

    private val repository = AutoChatRepository(ruleDAO, recordDAO)

    @Test
    fun `repeated tests on the same day persist distinct occurrences`() = runBlocking {
        val rule = AutoChatRule(enabled = true)
        val first = repository.createManualTestRecord(rule, "2026-11-20", 1000)
        val second = repository.createManualTestRecord(rule, "2026-11-20", 1000)

        assertEquals(-1, first.slotIndex)
        assertEquals(-2, second.slotIndex)
        assertNotEquals(first.id, second.id)
        assertEquals(2, records.size)
        assertFalse(rules.getValue(rule.id.toString()).enabled)
    }

    @Test
    fun `testing edited rule keeps saved rule and records draft target`() = runBlocking {
        val saved = AutoChatRule(enabled = true, randomPrompt = "Saved prompt")
        rules[saved.id.toString()] = AutoChatRuleEntity.fromModel(saved)
        val draft = saved.copy(randomPrompt = "Edited prompt", conversationId = kotlin.uuid.Uuid.random())

        val record = repository.createManualTestRecord(draft, "2026-11-20", 1000)

        assertEquals(AutoChatRuleEntity.fromModel(saved), rules[saved.id.toString()])
        assertEquals(draft.conversationId, record.conversationId)
        assertNotNull(records.find { it.id == record.id.toString() })
    }

    @Test(expected = IllegalStateException::class)
    fun `ignored insert fails before returning an occurrence for execution`() = runBlocking {
        rejectInsert = true
        repository.createManualTestRecord(AutoChatRule(), "2026-11-20", 1000)
        Unit
    }

    @Test
    fun `manual slots start over on a new day`() = runBlocking {
        val rule = AutoChatRule()
        repository.createManualTestRecord(rule, "2026-11-20", 1000)
        assertEquals(-1, repository.createManualTestRecord(rule, "2026-11-21", 2000).slotIndex)
    }

    @Test(expected = IllegalStateException::class)
    fun `exhausted manual slots fail instead of overflowing into scheduled slots`() = runBlocking {
        val rule = AutoChatRule()
        repository.createManualTestRecord(rule, "2026-11-20", 1000)
        records[0] = records[0].copy(slotIndex = Int.MIN_VALUE)
        repository.createManualTestRecord(rule, "2026-11-20", 1000)
        Unit
    }

    private inline fun <reified T> unusedDAO(): T = Proxy.newProxyInstance(
        T::class.java.classLoader,
        arrayOf(T::class.java),
    ) { _, method, _ -> error("Unexpected DAO call: ${method.name}") } as T
}
