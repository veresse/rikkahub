package me.rerere.companion.data.ws

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.testutil.FakeConnector
import me.rerere.companion.testutil.RecordingSink
import me.rerere.companion.testutil.keepOpen
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
    fun `reconnect clears cached device statuses`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                flowOf(
                    WsFrame.Open,
                    statusFrame(online = true),
                    WsFrame.Closed(1011, "lost"),
                ),
                keepOpen(WsFrame.Open),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()
        assertEquals(true, manager.devices.value["d1"]?.online)

        advanceTimeBy(1_000)
        runCurrent()

        assertEquals(2, connector.attempts.size)
        // 新连接不继承上一连接的设备在线缓存：等 device.status/report 重新填充。
        assertNull(manager.devices.value["d1"])
        manager.stop()
    }

    @Test
    fun `flat device report is accepted and marks the device online`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    WsFrame.Text(
                        """{"v":2,"type":"device.report","device_id":"d1","volume":30,"state":"idle"}""",
                    ),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()

        assertEquals(true, manager.devices.value["d1"]?.online)
        manager.stop()
    }

    @Test
    fun `server status snapshot does not resurrect an offline device`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    statusFrame(online = false, reason = "device_offline"),
                    WsFrame.Text(
                        """{"v":2,"type":"device.report","device_id":"d1",
                           "source":"server","volume":30,"state":"idle"}""",
                    ),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()

        // get_status 快照可能晚于 offline 事件：不得把设备翻回在线。
        assertFalse(manager.devices.value["d1"]!!.online)
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

    private val command = """{"v":2,"type":"remote.get_status","request_id":"r1","device_id":"d1"}"""

    @Test
    fun `send is refused before connecting and after the socket closes`() = runTest {
        val sink = RecordingSink()
        val connector = FakeConnector(
            mutableListOf(
                flowOf(
                    WsFrame.Open,
                    WsFrame.Writable(sink),
                    WsFrame.Closed(1011, "lost"),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })

        assertEquals(false, manager.send(command))

        manager.start()
        runCurrent()

        // 流已结束并进入重连等待：旧 sink 必须失效，不能往半死连接里写。
        assertEquals(false, manager.send(command))
        assertTrue(sink.sent.isEmpty())

        manager.stop()
    }

    @Test
    fun `send works while the socket is open and stops after stop`() = runTest {
        val sink = RecordingSink()
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    WsFrame.Writable(sink),
                    statusFrame(online = true),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        manager.start()
        runCurrent()

        assertEquals(true, manager.send(command))
        assertEquals(listOf(command), sink.sent)

        manager.stop()
        assertEquals(false, manager.send(command))
        assertEquals(1, sink.sent.size)
    }

    @Test
    fun `send reports refusal when the sink refuses the frame`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    WsFrame.Writable(WsSink { false }),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        manager.start()
        runCurrent()

        assertEquals(false, manager.send(command))
        manager.stop()
    }

    @Test
    fun `nested server snapshot does not resurrect an offline device`() = runTest {
        val connector = FakeConnector(
            mutableListOf(
                keepOpen(
                    WsFrame.Open,
                    statusFrame(online = false, reason = "device_offline"),
                    WsFrame.Text(
                        """{"v":2,"type":"device.report","device_id":"d1",
                           "report":{"source":"server","volume":30,"state":"idle"}}""",
                    ),
                ),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope)
        manager.start()
        runCurrent()

        // source 放在嵌套 report 里也必须识别。
        assertFalse(manager.devices.value["d1"]!!.online)
        manager.stop()
    }

    @Test
    fun `restarting does not let the old loop clear the new sink or connection`() = runTest {
        val oldSink = RecordingSink()
        val newSink = RecordingSink()
        val connector = FakeConnector(
            mutableListOf(
                // 旧连接：`Open`+句柄后卡在不可取消的等待里，取消不会立即收尾，
                // 模拟“旧 runLoop 的 finally 晚于新连接建立”。
                flow {
                    emit(WsFrame.Open)
                    emit(WsFrame.Writable(oldSink))
                    withContext(NonCancellable) { delay(50) }
                },
                keepOpen(WsFrame.Open, WsFrame.Writable(newSink)),
            ),
        )
        val manager = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        manager.start()
        runCurrent()
        assertEquals(ConnectionState.Connected, manager.connection.value)

        manager.stop()
        manager.start()
        runCurrent()
        assertEquals(ConnectionState.Connected, manager.connection.value)

        // 50ms 后旧协程才收尾：generation 守卫必须让新连接保持 Connected、sink 不被清空。
        advanceTimeBy(100)
        runCurrent()

        assertEquals(ConnectionState.Connected, manager.connection.value)
        assertTrue(manager.send(command))
        assertEquals(listOf(command), newSink.sent)
        assertTrue(oldSink.sent.isEmpty())
        manager.stop()
    }
}
