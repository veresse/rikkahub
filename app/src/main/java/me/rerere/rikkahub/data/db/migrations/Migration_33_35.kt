package me.rerere.rikkahub.data.db.migrations

import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import me.rerere.rikkahub.data.db.DatabaseMigrationTracker

/**
 * Fork version 33 contains Auto Chat; upstream version 33 instead changes the message index.
 * Keep the shipped fork schema unchanged and converge both histories at version 35.
 */
val Migration_33_35 = object : Migration(33, 35) {
    override fun migrate(db: SupportSQLiteDatabase) {
        // Idempotent CREATE statements also support databases from upstream version 33.
        Migration_32_33.migrate(db)
        DatabaseMigrationTracker.onMigrationStart(33, 35)
        try {
            db.execSQL(
                "CREATE INDEX IF NOT EXISTS `index_message_node_conversation_id_node_index` " +
                    "ON `message_node` (`conversation_id`, `node_index`)"
            )
            db.execSQL("DROP INDEX IF EXISTS `index_message_node_conversation_id`")
            db.execSQL("ALTER TABLE `workspaces` ADD COLUMN `storage_mounts` TEXT NOT NULL DEFAULT '[]'")
        } finally {
            DatabaseMigrationTracker.onMigrationEnd()
        }
    }
}
