package me.rerere.companion.data.repo

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

class ConversationRepositoryTest {

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

    private fun repository(manager: SessionManager): ConversationRepository =
        HttpConversationRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)

    @Test
    fun `list sends assistant query and parses rows`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """[{"id":"c1","assistant_id":"a1","title":"夜聊","is_pinned":false,
                    "created_at":1,"updated_at":2}]""",
            )
            val result = repository(manager(server.baseUrl)).list("a1")

            assertTrue(result is AppResult.Ok)
            val conversation = (result as AppResult.Ok).value.single()
            assertEquals("c1", conversation.id)
            assertEquals("夜聊", conversation.title)
            assertEquals("/api/conversations?assistant_id=a1", server.lastRequestUri)
        }
    }

    @Test
    fun `detail expands nodes and selected messages`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """
                {"id":"c1","assistant_id":"a1","title":"夜聊","is_pinned":false,
                 "created_at":1,"updated_at":2,
                 "nodes":[{"id":"n1","node_index":0,"select_index":0,"total_branches":1,
                   "messages":[{"id":"u1","role":"USER","parts":[{"text":"在吗"}]},
                               {"id":"m1","role":"ASSISTANT","parts":[{"text":"在的"}]}]}]}
                """.trimIndent(),
            )
            val result = repository(manager(server.baseUrl)).detail("c1")

            assertTrue(result is AppResult.Ok)
            val detail = (result as AppResult.Ok).value
            assertEquals(1, detail.nodes.size)
            val node = detail.nodes.single()
            assertEquals("n1", node.id)
            assertEquals("在吗", node.messages[0].parts[0].text)
            assertEquals("在的", node.messages[1].parts[0].text)
            assertEquals("/api/conversations/c1", server.lastRequestUri)
        }
    }

    @Test
    fun `correct message uses put with only text`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """{"status":"updated","node_id":"n1","message_id":"u1","text":"新的"}""",
            )
            val result = repository(manager(server.baseUrl)).correctMessage(
                conversationId = "c1",
                nodeId = "n1",
                messageId = "u1",
                text = "新的",
            )

            assertTrue(result is AppResult.Ok)
            assertEquals("PUT", server.lastRequestMethod)
            assertEquals("/api/conversations/c1/nodes/n1/messages/u1", server.lastRequestUri)
            assertEquals("""{"text":"新的"}""", server.lastRequestBody)
        }
    }

    @Test
    fun `select branch sends index query`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"status":"success","node_id":"n1","select_index":2}""")
            val result = repository(manager(server.baseUrl)).selectBranch("c1", "n1", 2)

            assertTrue(result is AppResult.Ok)
            assertEquals("POST", server.lastRequestMethod)
            assertEquals("/api/conversations/c1/nodes/n1/select?index=2", server.lastRequestUri)
        }
    }

    @Test
    fun `create sends assistant id and title`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"id":"c9","status":"created"}""")
            val result = repository(manager(server.baseUrl)).create("a1", "新对话")

            assertTrue(result is AppResult.Ok)
            assertEquals("c9", (result as AppResult.Ok).value)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"assistant_id\":\"a1\""))
            assertTrue(server.lastRequestBody.contains("\"title\":\"新对话\""))
        }
    }
}
