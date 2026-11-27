package me.rerere.companion.data.repo

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.test.runTest
import me.rerere.companion.core.AppResult
import me.rerere.companion.data.api.RetrofitApiFactory
import me.rerere.companion.data.session.Clock
import me.rerere.companion.data.session.SessionManager
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeSecureStore
import me.rerere.companion.testutil.TestHttpServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BackupRepositoryTest {

    private val previewJson = """{
        "preview_id":"p1",
        "summary":{"assistant_count":2,"conversation_count":3,
          "message_node_count":9,"total_message_versions":11,
          "memory_count":1,"unsupported_features":["MCP"]},
        "assistants_sample":["A"],
        "conversations_sample":["C"]
    }"""

    private val confirmJson = """{
        "status":"success","imported_assistants":2,"imported_conversations":3,
        "imported_nodes":9,"imported_memories":1
    }"""

    private suspend fun manager(url: String): SessionManager {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        manager.save(
            StoredSession(
                serverUrl = url,
                token = "t0",
                tokenId = "id0",
                username = "admin",
                expiresAtMillis = Long.MAX_VALUE,
            ),
        )
        return manager
    }

    private fun repo(session: SessionManager) = HttpBackupRepository(
        RetrofitApiFactory({ session.state.value }, {}),
        session,
    )

    @Test
    fun `preview streams the zip as multipart`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, previewJson)
            val session = manager(server.baseUrl)
            val repo = repo(session)

            val preview = (
                repo.preview(
                    ByteArrayInputStream("PK\u0003\u0004abc".toByteArray()),
                    "backup.zip",
                ) as AppResult.Ok
                ).value

            assertEquals("p1", preview.previewId)
            assertEquals(11, preview.summary.totalMessageVersions)
            assertEquals(listOf("MCP"), preview.summary.unsupportedFeatures)
            assertEquals("/api/backup/preview", server.lastRequestUri)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("filename=\"backup.zip\""))
            assertTrue(server.lastRequestBody.contains("PK"))
        }
    }

    @Test
    fun `confirm sends only the preview id`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, confirmJson)
            val session = manager(server.baseUrl)
            val repo = repo(session)

            val result = (repo.confirm("p1") as AppResult.Ok).value

            assertEquals("success", result.status)
            assertEquals(9, result.importedNodes)
            assertEquals("/api/backup/confirm", server.lastRequestUri)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"preview_id\":\"p1\""))
        }
    }

    @Test
    fun `export copies bytes with a single buffer loop`() = runTest {
        TestHttpServer().use { server ->
            val payload = ByteArray(20_000) { it.toByte() }
            server.enqueueBytes(200, payload, "application/zip")
            val session = manager(server.baseUrl)
            val repo = repo(session)
            val out = ByteArrayOutputStream()

            val result = repo.exportTo(out)

            assertEquals(20_000L, (result as AppResult.Ok).value)
            assertEquals(20_000, out.size())
            assertEquals(payload.toList(), out.toByteArray().toList())
            assertEquals("/api/backup/export", server.lastRequestUri)
            assertEquals("GET", server.lastRequestMethod)
        }
    }

    @Test
    fun `preview expiry maps to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(400, """{"detail":"preview_expired"}""")
            val session = manager(server.baseUrl)
            val repo = repo(session)

            val result = repo.preview(
                ByteArrayInputStream(byteArrayOf(1)),
                "backup.zip",
            )

            assertEquals(
                "预览已过期，请重新选择文件",
                (result as AppResult.Err).error.message,
            )
        }
    }

    @Test
    fun `oversized upload maps to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(413, """{"detail":"file_too_large"}""")
            val session = manager(server.baseUrl)
            val repo = repo(session)

            val result = repo.preview(
                ByteArrayInputStream(ByteArray(16)),
                "backup.zip",
            )

            assertEquals(
                "文件超过服务器允许的大小",
                (result as AppResult.Err).error.message,
            )
        }
    }
}
