package me.rerere.rikkahub.data.db.dao

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow
import me.rerere.rikkahub.data.db.entity.AutoChatRecordEntity

@Dao
interface AutoChatRecordDAO {
    @Query("SELECT * FROM auto_chat_records ORDER BY created_at DESC")
    fun getAll(): Flow<List<AutoChatRecordEntity>>

    @Query("SELECT * FROM auto_chat_records WHERE rule_id = :ruleId ORDER BY scheduled_time DESC")
    fun getByRuleId(ruleId: String): Flow<List<AutoChatRecordEntity>>

    @Query("SELECT * FROM auto_chat_records ORDER BY scheduled_time DESC LIMIT :limit")
    fun getRecentRecords(limit: Int = 100): Flow<List<AutoChatRecordEntity>>

    @Query("SELECT * FROM auto_chat_records WHERE id = :id")
    suspend fun getById(id: String): AutoChatRecordEntity?

    @Query("SELECT * FROM auto_chat_records WHERE message_id = :messageId")
    suspend fun getByMessageId(messageId: String): AutoChatRecordEntity?

    @Query(
        """
        SELECT COUNT(*) FROM auto_chat_records 
        WHERE conversation_id = :conversationId 
          AND target_date = :targetDate 
          AND status = 'SUCCEEDED'
          AND occurrence_type != 'MANUAL_TEST'
        """
    )
    suspend fun getSuccessfulCountOnDate(conversationId: String, targetDate: String): Int

    @Query(
        """
        SELECT * FROM auto_chat_records 
        WHERE conversation_id = :conversationId 
          AND status = 'SUCCEEDED'
          AND occurrence_type != 'MANUAL_TEST'
        ORDER BY COALESCE(actual_time, scheduled_time) DESC 
        LIMIT 1
        """
    )
    suspend fun getLastSuccessfulRecord(conversationId: String): AutoChatRecordEntity?

    @Query(
        """
        SELECT * FROM auto_chat_records 
        WHERE rule_id = :ruleId 
          AND target_date = :targetDate 
          AND slot_index = :slotIndex 
          AND occurrence_type = :occurrenceType
        LIMIT 1
        """
    )
    suspend fun getOccurrence(
        ruleId: String,
        targetDate: String,
        slotIndex: Int,
        occurrenceType: String
    ): AutoChatRecordEntity?

    @Query(
        """
        UPDATE auto_chat_records 
        SET status = :toStatus, actual_time = :actualTime 
        WHERE id = :id AND status = :fromStatus
        """
    )
    suspend fun claimOccurrence(
        id: String,
        fromStatus: String,
        toStatus: String,
        actualTime: Long
    ): Int

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(record: AutoChatRecordEntity): Long

    @Update
    suspend fun update(record: AutoChatRecordEntity)

    @Delete
    suspend fun delete(record: AutoChatRecordEntity)

    @Query("DELETE FROM auto_chat_records")
    suspend fun deleteAll()

    @Query("DELETE FROM auto_chat_records WHERE created_at < :cutoffMillis")
    suspend fun deleteOlderThan(cutoffMillis: Long)

    @Query("DELETE FROM auto_chat_records WHERE rule_id = :ruleId AND status = 'SCHEDULED'")
    suspend fun deleteScheduledByRuleId(ruleId: String)

    @Query("DELETE FROM auto_chat_records WHERE status = 'SCHEDULED'")
    suspend fun deleteAllScheduled()
}
