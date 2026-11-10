package me.rerere.rikkahub.data.repository

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import me.rerere.rikkahub.data.db.dao.AutoChatRecordDAO
import me.rerere.rikkahub.data.db.dao.AutoChatRuleDAO
import me.rerere.rikkahub.data.db.entity.AutoChatRecordEntity
import me.rerere.rikkahub.data.db.entity.AutoChatRuleEntity
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.AutoChatStatus
import me.rerere.rikkahub.data.model.AutoChatType
import kotlin.uuid.Uuid

class AutoChatRepository(
    private val ruleDAO: AutoChatRuleDAO,
    private val recordDAO: AutoChatRecordDAO,
) {
    fun getAllRules(): Flow<List<AutoChatRule>> =
        ruleDAO.getAll().map { list -> list.map { it.toModel() } }

    suspend fun getAllRulesList(): List<AutoChatRule> =
        ruleDAO.getAllList().map { it.toModel() }

    suspend fun getEnabledRules(): List<AutoChatRule> =
        ruleDAO.getEnabledRules().map { it.toModel() }

    suspend fun getRuleById(id: Uuid): AutoChatRule? =
        ruleDAO.getById(id.toString())?.toModel()

    suspend fun getRulesByConversationId(conversationId: Uuid): List<AutoChatRule> =
        ruleDAO.getByConversationId(conversationId.toString()).map { it.toModel() }

    suspend fun insertRule(rule: AutoChatRule) {
        ruleDAO.insert(AutoChatRuleEntity.fromModel(rule))
    }

    suspend fun updateRule(rule: AutoChatRule) {
        ruleDAO.update(AutoChatRuleEntity.fromModel(rule.copy(updatedAt = System.currentTimeMillis())))
    }

    suspend fun deleteRule(id: Uuid) {
        ruleDAO.deleteById(id.toString())
    }

    suspend fun disableAllRules() {
        ruleDAO.disableAllRules()
    }

    fun getAllRecords(): Flow<List<AutoChatRecord>> =
        recordDAO.getAll().map { list -> list.map { it.toModel() } }

    fun getRecordsByRuleId(ruleId: Uuid): Flow<List<AutoChatRecord>> =
        recordDAO.getByRuleId(ruleId.toString()).map { list -> list.map { it.toModel() } }

    fun getRecentRecords(limit: Int = 100): Flow<List<AutoChatRecord>> =
        recordDAO.getRecentRecords(limit).map { list -> list.map { it.toModel() } }

    suspend fun getRecordById(id: Uuid): AutoChatRecord? =
        recordDAO.getById(id.toString())?.toModel()

    suspend fun getRecordByMessageId(messageId: Uuid): AutoChatRecord? =
        recordDAO.getByMessageId(messageId.toString())?.toModel()

    suspend fun getSuccessfulCountOnDate(conversationId: Uuid, targetDate: String): Int =
        recordDAO.getSuccessfulCountOnDate(conversationId.toString(), targetDate)

    suspend fun getLastSuccessfulRecord(conversationId: Uuid): AutoChatRecord? =
        recordDAO.getLastSuccessfulRecord(conversationId.toString())?.toModel()

    suspend fun getOccurrence(
        ruleId: Uuid,
        targetDate: String,
        slotIndex: Int,
        occurrenceType: AutoChatType
    ): AutoChatRecord? =
        recordDAO.getOccurrence(
            ruleId = ruleId.toString(),
            targetDate = targetDate,
            slotIndex = slotIndex,
            occurrenceType = occurrenceType.name
        )?.toModel()

    suspend fun claimOccurrence(
        id: Uuid,
        fromStatus: AutoChatStatus,
        toStatus: AutoChatStatus,
        actualTime: Long
    ): Boolean =
        recordDAO.claimOccurrence(
            id = id.toString(),
            fromStatus = fromStatus.name,
            toStatus = toStatus.name,
            actualTime = actualTime
        ) > 0

    suspend fun insertRecord(record: AutoChatRecord): Long =
        recordDAO.insert(AutoChatRecordEntity.fromModel(record))

    suspend fun updateRecord(record: AutoChatRecord) {
        recordDAO.update(AutoChatRecordEntity.fromModel(record))
    }

    suspend fun deleteRecord(record: AutoChatRecord) {
        recordDAO.delete(AutoChatRecordEntity.fromModel(record))
    }

    suspend fun deleteAllRecords() {
        recordDAO.deleteAll()
    }

    suspend fun deleteOlderRecords(cutoffMillis: Long) {
        recordDAO.deleteOlderThan(cutoffMillis)
    }

    suspend fun deleteScheduledByRuleId(ruleId: Uuid) {
        recordDAO.deleteScheduledByRuleId(ruleId.toString())
    }

    suspend fun deleteAllScheduledRecords() {
        recordDAO.deleteAllScheduled()
    }
}
