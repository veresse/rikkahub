package me.rerere.companion.data.ws

import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.rerere.companion.data.store.StoredSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AppWsManagerTest {

    private val session = StoredSession(
        serverUrl = "http://example.com/",
        token = "t0",
        tokenId = "id0",
        username = "admin",
        expiresAtMillis = Long.MAX_VALUE,
    )

    private class FakeConnector(
        private val scripts: MutableList<Flow<WsFrame>>,
    ) : WsConnector {
        val attempts = mutableListOf<String>()

        override fun connect(url: String, token: String): Flow<WsFrame> {
            attempts += url
            return if (scripts.isEmpty()) flow { awaitCancellation() } else scripts.removeAt(0)
        }
    }

    private fun keepOpen(vararg frames: WsFrame): Flow<WsFrame> = flow {
        frames.forEach { emit(it) }
        awaitCancellation()
    }

    private fun statusFrame(online: Boolean, reason: String = ""): WsFrame.Text =
        WsFrame.Text(
            """{"v":2,"type":"device.status","device_id":"d1","online":$online,"reason":"$reason"}""",
        )

    private fun turnFrame(state: String, turnKey: String): WsFrame.Text =
        WsFrame.Text(
            """{"v":2,"type":"conversation.turn_state","conversation_id":"c1",
               "state":"$state","source":"app","turn_key":"$turnKey"}""",
        )

    @Test
    fun `connects and applies device report and turn state`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    statusFrame(online = false, reason = "device_revoked"),
                    WsFrame.Text(
                        """{"v":2,"type":"device.report","device_id":"d1","report":{"state":"idle"}}""",
                    ),
                    turnFrame("generating", "k1"),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        val events = mutableListOf<WsEvent>()
        backgroundScope.launch { manager.events.collect { events += it } }
        runCurrent()

        manager.start()
        runCurrent()

        assertEquals(ConnectionState.Connected, manager.connection.value)
        assertEquals("ws://example.com/app/ws", connector.attempts.single())
        assertEquals(true, manager.devices.value["d1"]?.online)
        assertEquals("k1", manager.turnStates.value["c1"]?.turnKey)
        assertTrue(events.any { it is WsEvent.DeviceStatus && !it.online })
        assertTrue(events.any { it is WsEvent.DeviceReport })
        assertTrue(events.any { it is WsEvent.TurnStateChanged })

        manager.stop()
    }

    @Test
    fun `stale idle with different turn key is ignored`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    turnFrame("generating", "new-key"),
                    turnFrame("idle", "old-key"),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()

        assertEquals("new-key", manager.turnStates.value["c1"]?.turnKey)

        // 同一 key 的 idle 必须清掉生成中标记。
        val sameKey = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    turnFrame("generating", "k1"),
                    turnFrame("idle", "k1"),
                ),
            ),
        )
        manager.stop()
        val second = AppWsManager(sameKey, { session }, {}, backgroundScope)
        second.start()
        runCurrent()

        assertNull(second.turnStates.value["c1"])
        second.stop()
    }

    @Test
    fun `backoff schedule follows 1 2 4 and caps at 30 seconds`() {
        assertEquals(
            listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L),
            (0..6).map(AppWsManager::backoffDelay),
        )
    }

    @Test
    fun `accept then immediate close keeps exponential backoff`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                keepOpen(WsFrame.Open),
            ),
        )
        // 连接存活时间为 0：接受后立刻关闭不得把退避重置成 1s。
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        manager.start()
        runCurrent()
        assertEquals(ConnectionState.Retrying(1, 1_000), manager.connection.value)

        advanceTimeBy(1_000)
        runCurrent()
        assertEquals(ConnectionState.Retrying(2, 2_000), manager.connection.value)

        advanceTimeBy(2_000)
        runCurrent()
        assertEquals(ConnectionState.Retrying(3, 4_000), manager.connection.value)

        advanceTimeBy(4_000)
        runCurrent()
        assertEquals(4, connector.attempts.size)
        assertEquals(ConnectionState.Connected, manager.connection.value)

        manager.stop()
    }

    @Test
    fun `connection stable for 30 seconds resets backoff`() = runTest {
        var clock = 0L
        val connector = FakeConnector(
            mutableListOf(
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                flow {
                    emit(WsFrame.Open)
                    clock = 30_000L
                    emit(WsFrame.Closed(1011, "lost"))
                },
                keepOpen(WsFrame.Open),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { clock })
        manager.start()
        runCurrent()
        assertEquals(ConnectionState.Retrying(1, 1_000), manager.connection.value)

        advanceTimeBy(1_000)
        runCurrent()
        // 稳定 30s 后重连，退避应回到第 1 档而不是继续翻倍。
        assertEquals(ConnectionState.Retrying(1, 1_000), manager.connection.value)

        advanceTimeBy(1_000)
        runCurrent()
        assertEquals(ConnectionState.Connected, manager.connection.value)

        manager.stop()
    }

    @Test
    fun `reconnect clears stale turn state`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                flowOf(
                    WsFrame.Open,
                    turnFrame("generating", "k1"),
                    WsFrame.Closed(1011, "lost"),
                ),
                keepOpen(WsFrame.Open),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        manager.start()
        runCurrent()

        // 断线后立刻清空，而不是等下一次连接成功；否则重连间隔内会话一直“生成中”。
        assertEquals(ConnectionState.Retrying(1, 1_000), manager.connection.value)
        assertTrue(manager.turnStates.value.isEmpty())

        advanceTimeBy(1_000)
        runCurrent()
        assertEquals(ConnectionState.Connected, manager.connection.value)
        assertTrue(manager.turnStates.value.isEmpty())

        manager.stop()
    }

    @Test
    fun `4401 invalidates token and does not reconnect`() = runTest {
        val connector = FakeConnector(
            mutableListOf(flowOf(WsFrame.Open, WsFrame.Closed(4401, "token_revoked"))),
        )
        val unauthorized = mutableListOf<String>()
        val manager = AppWsManager(connector, { session }, { unauthorized += it }, backgroundScope)
        manager.start()
        runCurrent()

        assertEquals(listOf("t0"), unauthorized)
        assertEquals(ConnectionState.Stopped, manager.connection.value)

        advanceTimeBy(120_000)
        runCurrent()
        assertEquals(1, connector.attempts.size)
    }

    @Test
    fun `network available retries immediately`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                flowOf(WsFrame.Open, WsFrame.Closed(1011, "lost")),
                keepOpen(WsFrame.Open),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()
        assertEquals(1, connector.attempts.size)

        // 不等 1s，网络恢复信号立即触发下一次连接。
        manager.onNetworkAvailable()
        runCurrent()
        assertEquals(2, connector.attempts.size)

        manager.onNetworkAvailable()
        runCurrent()
        assertEquals(3, connector.attempts.size)
        assertEquals(ConnectionState.Connected, manager.connection.value)

        manager.stop()
    }

    @Test
    fun `stop closes connection and clears cached state`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    statusFrame(online = true),
                    turnFrame("generating", "k1"),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()
        assertEquals(ConnectionState.Connected, manager.connection.value)
        assertEquals(true, manager.devices.value["d1"]?.online)
        assertEquals("k1", manager.turnStates.value["c1"]?.turnKey)

        manager.stop()
        assertEquals(ConnectionState.Stopped, manager.connection.value)
        // 登出/停止后不得把上一个账号的状态留给下一个账号。
        assertTrue(manager.devices.value.isEmpty())
        assertTrue(manager.turnStates.value.isEmpty())

        advanceTimeBy(60_000)
        runCurrent()
        assertEquals(1, connector.attempts.size)
    }

    @Test
    fun `builds ws and wss urls`() {
        assertEquals("ws://host:8000/app/ws", AppWsManager.wsUrl("http://host:8000/"))
        assertEquals("wss://host/app/ws", AppWsManager.wsUrl("https://host"))
    }
}
