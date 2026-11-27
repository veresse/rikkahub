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

class DeviceRepositoryTest {

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

    @Test
    fun `lists devices with online flag`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """[{"id":"d1","name":"客厅","online":true,"volume":60,"state":"idle",
                   "is_active":true,"protocol_version":2,"firmware_version":"2.0.0",
                   "bound_assistant_id":"a1","pan_offset":1.5,"tilt_offset":-2.0,
                   "idle_enabled":false,"last_seen_at":1700000000000}]""",
            )
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val result = repo.list()

            val device = (result as AppResult.Ok).value.single()
            assertEquals("d1", device.id)
            assertEquals("客厅", device.name)
            assertTrue(device.online)
            assertEquals(true, device.isActive)
            assertEquals(60, device.volume)
            assertEquals("a1", device.boundAssistantId)
            assertEquals(1.5, device.panOffset, 0.0)
            assertEquals(-2.0, device.tiltOffset, 0.0)
            assertEquals(false, device.idleEnabled)
            assertEquals(1700000000000L, device.lastSeenAt)
            assertEquals("/api/devices", server.lastRequestUri)
            assertEquals("GET", server.lastRequestMethod)
        }
    }

    @Test
    fun `pair code parses expiry seconds`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"code":"123456","expires_in":120}""")
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val result = repo.createPairCode(assistantId = "a1", conversationId = "c1")

            val pair = (result as AppResult.Ok).value
            assertEquals("123456", pair.code)
            assertEquals(120, pair.expiresIn)
            assertEquals("/api/devices/pair-code", server.lastRequestUri)
            assertEquals("POST", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"assistant_id\":\"a1\""))
            assertTrue(server.lastRequestBody.contains("\"conversation_id\":\"c1\""))
        }
    }

    @Test
    fun `bind sends assistant and conversation ids`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"status":"bound"}""")
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val result = repo.bind("d1", "a1", "c1")

            assertTrue(result is AppResult.Ok)
            assertEquals("/api/devices/bind", server.lastRequestUri)
            assertEquals("PUT", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"device_id\":\"d1\""))
            assertTrue(server.lastRequestBody.contains("\"bound_assistant_id\":\"a1\""))
            assertTrue(server.lastRequestBody.contains("\"bound_conversation_id\":\"c1\""))
        }
    }

    @Test
    fun `rename and revoke hit the device path`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"status":"updated"}""")
            server.enqueue(200, """{"status":"revoked"}""")
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            assertTrue(repo.rename("d1", "书房机器人") is AppResult.Ok)
            assertEquals("/api/devices/d1", server.lastRequestUri)
            assertEquals("PUT", server.lastRequestMethod)
            assertTrue(server.lastRequestBody.contains("\"name\":\"书房机器人\""))

            assertTrue(repo.revoke("d1") is AppResult.Ok)
            assertEquals("DELETE", server.lastRequestMethod)
            assertEquals("/api/devices/d1", server.lastRequestUri)
        }
    }

    @Test
    fun `catalog parses categories limits and per action supports`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(
                200,
                """{"version":"2.0.0",
                   "categories":{"basic":"复位与看看","mimic":"姿态模仿"},
                   "limits":{"pan":{"min":-90.0,"max":90.0},
                             "tilt":{"min":-20.0,"max":45.0},
                             "volume":{"min":0,"max":100,"default":70}},
                   "actions":[
                     {"name":"home","label_zh":"复位","category":"basic",
                      "supports":{"intensity":false,"speed":true,"repeat":false},
                      "max_duration_ms":3000},
                     {"name":"dance","label_zh":"跳舞","category":"mimic",
                      "supports":{"intensity":true,"speed":true,"repeat":true},
                      "max_duration_ms":5000}
                   ]}""",
            )
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val catalog = (repo.catalog() as AppResult.Ok).value

            assertEquals("2.0.0", catalog.version)
            assertEquals("复位与看看", catalog.categories["basic"])
            assertEquals(45.0, catalog.limits.tilt.max, 0.0)
            assertEquals(70, catalog.limits.volume.default)
            val home = catalog.actions.single { it.name == "home" }
            assertEquals("复位", home.labelZh)
            assertEquals(false, home.supports.intensity)
            assertEquals(true, home.supports.speed)
            assertEquals(false, home.supports.repeat)
            assertEquals(3000, home.maxDurationMs)
            assertEquals(5000, catalog.actions.single { it.name == "dance" }.maxDurationMs)
            assertEquals("/api/actions/catalog", server.lastRequestUri)
            assertEquals("GET", server.lastRequestMethod)
        }
    }

    @Test
    fun `catalog tolerates missing optional fields`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(200, """{"actions":[{"name":"nod"}]}""")
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val catalog = (repo.catalog() as AppResult.Ok).value

            val nod = catalog.actions.single()
            assertEquals("", nod.labelZh)
            assertEquals(3000, nod.maxDurationMs)
            assertEquals(-20.0, catalog.limits.tilt.min, 0.0)
            assertEquals(45.0, catalog.limits.tilt.max, 0.0)
        }
    }

    @Test
    fun `device_offline error maps to readable message`() = runTest {
        TestHttpServer().use { server ->
            server.enqueue(409, """{"detail":"device_offline"}""")
            val session = manager(server.baseUrl)
            val repo = HttpDeviceRepository(RetrofitApiFactory({ session.state.value }, {}), session)

            val result = repo.list()

            val error = (result as AppResult.Err).error
            assertEquals("设备不在线", error.message)
        }
    }

    @Test
    fun `list without session is refused before hitting the network`() = runTest {
        val manager = SessionManager(FakeSecureStore(), Clock { 0L })
        val repo = HttpDeviceRepository(RetrofitApiFactory({ manager.state.value }, {}), manager)

        val result = repo.list()

        assertEquals("请先登录", (result as AppResult.Err).error.message)
    }
}
