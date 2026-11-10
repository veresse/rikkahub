package me.rerere.rikkahub.data.db.migrations

import android.util.Log
import androidx.room.migration.AutoMigrationSpec
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import me.rerere.rikkahub.data.db.DatabaseMigrationTracker

private const val TAG = "Migration_32_33"

/** 新增 auto_chat_rules 与 auto_chat_records 表 */
class Migration_32_33_Spec : AutoMigrationSpec

val Migration_32_33 = object : Migration(32, 33) {
    override fun migrate(db: SupportSQLiteDatabase) {
        Log.i(TAG, "migrate: start migrate from 32 to 33 (auto chat)")
        DatabaseMigrationTracker.onMigrationStart(32, 33)
        db.beginTransaction()
        try {
            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS `auto_chat_rules` (
                    `id` TEXT NOT NULL,
                    `name` TEXT NOT NULL,
                    `assistant_id` TEXT NOT NULL,
                    `conversation_id` TEXT NOT NULL,
                    `enabled` INTEGER NOT NULL,
                    `fixed_times` TEXT NOT NULL,
                    `random_enabled` INTEGER NOT NULL,
                    `random_days_of_week` TEXT NOT NULL,
                    `random_window_start` TEXT NOT NULL,
                    `random_window_end` TEXT NOT NULL,
                    `random_count_per_day` INTEGER NOT NULL,
                    `min_interval_minutes` INTEGER NOT NULL,
                    `quiet_hours_enabled` INTEGER NOT NULL,
                    `quiet_hours_start` TEXT NOT NULL,
                    `quiet_hours_end` TEXT NOT NULL,
                    `daily_max_messages` INTEGER NOT NULL,
                    `skip_if_unread` INTEGER NOT NULL,
                    `fixed_prompt` TEXT NOT NULL,
                    `random_prompt` TEXT NOT NULL,
                    `max_delay_minutes` INTEGER NOT NULL,
                    `enable_notification` INTEGER NOT NULL,
                    `show_preview` INTEGER NOT NULL,
                    `version` INTEGER NOT NULL,
                    `created_at` INTEGER NOT NULL,
                    `updated_at` INTEGER NOT NULL,
                    PRIMARY KEY(`id`)
                )
                """.trimIndent()
            )
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_auto_chat_rules_conversation_id` ON `auto_chat_rules` (`conversation_id`)")
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_auto_chat_rules_assistant_id` ON `auto_chat_rules` (`assistant_id`)")

            db.execSQL(
                """
                CREATE TABLE IF NOT EXISTS `auto_chat_records` (
                    `id` TEXT NOT NULL,
                    `rule_id` TEXT NOT NULL,
                    `assistant_id` TEXT NOT NULL,
                    `conversation_id` TEXT NOT NULL,
                    `occurrence_type` TEXT NOT NULL,
                    `target_date` TEXT NOT NULL,
                    `slot_index` INTEGER NOT NULL,
                    `scheduled_time` INTEGER NOT NULL,
                    `actual_time` INTEGER,
                    `status` TEXT NOT NULL,
                    `message_id` TEXT,
                    `error_message` TEXT,
                    `skip_reason` TEXT,
                    `context_summary` TEXT,
                    `created_at` INTEGER NOT NULL,
                    PRIMARY KEY(`id`),
                    FOREIGN KEY(`rule_id`) REFERENCES `auto_chat_rules`(`id`) ON UPDATE NO ACTION ON DELETE CASCADE
                )
                """.trimIndent()
            )
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_auto_chat_records_rule_id` ON `auto_chat_records` (`rule_id`)")
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_auto_chat_records_conversation_id_target_date` ON `auto_chat_records` (`conversation_id`, `target_date`)")
            db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_auto_chat_records_rule_id_target_date_slot_index_occurrence_type` ON `auto_chat_records` (`rule_id`, `target_date`, `slot_index`, `occurrence_type`)")

            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
            DatabaseMigrationTracker.onMigrationEnd()
        }
    }
}
