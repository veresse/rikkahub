package me.rerere.rikkahub.data.db.dao

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow
import me.rerere.rikkahub.data.db.entity.AutoChatRuleEntity

@Dao
interface AutoChatRuleDAO {
    @Query("SELECT * FROM auto_chat_rules ORDER BY created_at DESC")
    fun getAll(): Flow<List<AutoChatRuleEntity>>

    @Query("SELECT * FROM auto_chat_rules ORDER BY created_at DESC")
    suspend fun getAllList(): List<AutoChatRuleEntity>

    @Query("SELECT * FROM auto_chat_rules WHERE enabled = 1")
    suspend fun getEnabledRules(): List<AutoChatRuleEntity>

    @Query("SELECT * FROM auto_chat_rules WHERE id = :id")
    suspend fun getById(id: String): AutoChatRuleEntity?

    @Query("SELECT * FROM auto_chat_rules WHERE conversation_id = :conversationId")
    suspend fun getByConversationId(conversationId: String): List<AutoChatRuleEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(rule: AutoChatRuleEntity)

    @Update
    suspend fun update(rule: AutoChatRuleEntity)

    @Delete
    suspend fun delete(rule: AutoChatRuleEntity)

    @Query("DELETE FROM auto_chat_rules WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("UPDATE auto_chat_rules SET enabled = 0")
    suspend fun disableAllRules()
}
