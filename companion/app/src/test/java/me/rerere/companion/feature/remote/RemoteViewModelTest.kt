package me.rerere.companion.feature.remote

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import me.rerere.companion.data.api.dto.ActionCatalogDto
import me.rerere.companion.data.api.dto.ActionSupportsDto
import me.rerere.companion.data.api.dto.CatalogActionDto
import me.rerere.companion.data.api.dto.DeviceDto
import me.rerere.companion.data.store.StoredSession
import me.rerere.companion.data.ws.AppWsManager
import me.rerere.companion.data.ws.ConnectionState
import me.rerere.companion.data.ws.WsConnector
import me.rerere.companion.data.ws.WsFrame
import me.rerere.companion.testutil.FakeDeviceRepository
import me.rerere.companion.testutil.MainDispatcherRule
import me.rerere.companion.testutil.RecordingSink
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * 遥控页状态机测试。
 *
 * 用真实的 [AppWsManager] + 假传输层驱动：`send` 落到 [RecordingSink]，
 * 服务端帧通过 [Harness.push] 注入，覆盖防抖、ack/result 匹配、离线与断线清理。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class RemoteViewModelTest {

    @get:Rule
    val main = MainDispatcherRule()

    private val session = StoredSession(
        serverUrl = "http://example.com/",
        token = "t0",
        tokenId = "id0",
        username = "admin",
        expiresAtMillis = Long.MAX_VALUE,
    )

    private class Harness(
        val sink: RecordingSink,
        val frames: Channel<WsFrame>,
        private val reconnectedFrames: Channel<WsFrame>,
        val ws: AppWsManager,
        val repo: FakeDeviceRepository,
        val vm: RemoteViewModel,
    ) {
        /** 当前连接对应的下行通道；断线后切到重连通道。 */
        private var active = frames

        fun push(json: String) {
            active.trySend(WsFrame.Text(json))
        }

        fun disconnect() {
            active.close()
            active = reconnectedFrames
        }

        /** 已发出的命令，按 `type` 过滤并解析成 JSON。 */
        fun sent(type: String): List<JsonObject> = sink.sent
            .map { Json.parseToJsonElement(it).jsonObject }
            .filter { it.string("type") == type }
    }

    private fun TestScope.harness(
        networkOnline: Boolean = true,
        deviceOnline: Boolean = true,
        deviceVolume: Int = 40,
        panOffset: Double = 0.0,
        tiltOffset: Double = 0.0,
        catalog: ActionCatalogDto = sampleCatalog(),
    ): Harness {
        val sink = RecordingSink()
        val frames = Channel<WsFrame>(Channel.UNLIMITED)
        val reconnectedFrames = Channel<WsFrame>(Channel.UNLIMITED)
        var attempts = 0
        val connector = WsConnector { _, _ ->
            attempts += 1
            val framesForAttempt = if (attempts == 1) frames else reconnectedFrames
            flow {
                emit(WsFrame.Open)
                emit(WsFrame.Writable(sink))
                for (frame in framesForAttempt) emit(frame)
            }
        }
        val ws = AppWsManager(connector, { session }, {}, backgroundScope, now = { 0L })
        val repo = FakeDeviceRepository()
        repo.devices = mutableListOf(
            DeviceDto(
                id = DEVICE_ID,
                name = "客厅机器人",
                online = deviceOnline,
                volume = deviceVolume,
                panOffset = panOffset,
                tiltOffset = tiltOffset,
                state = "idle",
            ),
        )
        repo.catalog = catalog
        val vm = RemoteViewModel(
            deviceId = DEVICE_ID,
            deviceName = "客厅机器人",
            repository = repo,
            ws = ws,
            isOnline = { networkOnline },
        )

        ws.start()
        main.scheduler.runCurrent()
        vm.start()
        main.scheduler.runCurrent()
        return Harness(sink, frames, reconnectedFrames, ws, repo, vm)
    }

    private fun sampleCatalog(): ActionCatalogDto = ActionCatalogDto(
        version = "1",
        categories = mapOf("basic" to "基础"),
        actions = listOf(
            CatalogActionDto(
                name = "nod",
                labelZh = "点头",
                category = "basic",
                supports = ActionSupportsDto(intensity = true, speed = true, repeat = true),
            ),
        ),
    )

    private fun ack(
        requestId: String,
        ok: Boolean,
        error: String? = null,
        actionId: String? = null,
    ): String = buildJsonObject {
        put("v", 2)
        put("type", "remote.ack")
        put("request_id", requestId)
        put("ok", ok)
        if (error != null) put("error", error)
        if (actionId != null) put("action_id", actionId)
    }.toString()

    private fun actionResult(
        actionId: String,
        status: String,
        detail: String = "",
        source: String = "app",
    ): String = buildJsonObject {
        put("v", 2)
        put("type", "action.result")
        put("device_id", DEVICE_ID)
        put("action_id", actionId)
        put("status", status)
        put("detail", detail)
        put("source", source)
    }.toString()

    private fun report(
        volume: Int? = null,
        pan: Double? = null,
        tilt: Double? = null,
        idle: Boolean? = null,
        panOffset: Double? = null,
        tiltOffset: Double? = null,
        state: String = "idle",
        source: String? = null,
    ): String = buildJsonObject {
        put("v", 2)
        put("type", "device.report")
        put("device_id", DEVICE_ID)
        put("state", state)
        if (source != null) put("source", source)
        if (volume != null) put("volume", volume)
        if (pan != null) put("pan", pan)
        if (tilt != null) put("tilt", tilt)
        if (idle != null) put("idle", idle)
        if (panOffset != null) put("pan_offset", panOffset)
        if (tiltOffset != null) put("tilt_offset", tiltOffset)
    }.toString()

    private fun deviceStatus(online: Boolean, reason: String): String = buildJsonObject {
        put("v", 2)
        put("type", "device.status")
        put("device_id", DEVICE_ID)
        put("online", online)
        put("reason", reason)
    }.toString()

    @Test
    fun `volume is debounced to one command`() = runTest {
        val h = harness()

        h.vm.onVolumeChange(30)
        h.vm.onVolumeChange(40)
        h.vm.onVolumeChange(50)
        h.vm.onVolumeChange(60)
        main.scheduler.advanceTimeBy(199)
        main.scheduler.runCurrent()
        assertTrue(h.sent("remote.volume.set").isEmpty())
        assertEquals(60, h.vm.state.value.volume)

        main.scheduler.advanceTimeBy(1)
        main.scheduler.runCurrent()
        val sent = h.sent("remote.volume.set")
        assertEquals(1, sent.size)
        assertEquals(60, sent.single().int("volume"))
    }

    @Test
    fun `volume commit sends immediately and cancels pending`() = runTest {
        val h = harness()

        h.vm.onVolumeChange(55)
        h.vm.commitVolume()
        main.scheduler.runCurrent()
        assertEquals(1, h.sent("remote.volume.set").size)

        // 防抖任务已被取消：不能再补发第二次。
        main.scheduler.advanceTimeBy(1_000)
        main.scheduler.runCurrent()
        assertEquals(1, h.sent("remote.volume.set").size)
    }

    @Test
    fun `pan and tilt are clamped and debounced`() = runTest {
        val h = harness()

        h.vm.onPanTiltChange(pan = 200.0, tilt = -80.0)
        main.scheduler.advanceTimeBy(199)
        main.scheduler.runCurrent()
        assertTrue(h.sent("remote.servo.move").isEmpty())

        main.scheduler.advanceTimeBy(1)
        main.scheduler.runCurrent()
        val servo = h.sent("remote.servo.move").single()
        assertEquals(90.0, servo.double("pan"), 1e-9)
        assertEquals(-20.0, servo.double("tilt"), 1e-9)
    }

    @Test
    fun `mute toggles to zero and restores the previous volume`() = runTest {
        val h = harness(deviceVolume = 40)

        h.vm.toggleMute()
        main.scheduler.runCurrent()
        assertEquals(0, h.vm.state.value.volume)
        assertEquals(40, h.vm.state.value.mutedBefore)
        assertEquals(0, h.sent("remote.volume.set").single().int("volume"))

        h.vm.toggleMute()
        main.scheduler.runCurrent()
        assertEquals(40, h.vm.state.value.volume)
        assertEquals(40, h.sent("remote.volume.set").last().int("volume"))
    }

    @Test
    fun `offline device blocks every command`() = runTest {
        val h = harness(deviceOnline = false)

        h.vm.playAction("nod")
        main.scheduler.runCurrent()

        assertTrue(h.sent("remote.action.play").isEmpty())
        assertEquals(RemoteResultKind.FAILED, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("离线"))
        assertNull(h.vm.state.value.sendingAction)
    }

    @Test
    fun `offline network blocks every command`() = runTest {
        val h = harness(networkOnline = false)

        h.vm.playAction("nod")
        main.scheduler.runCurrent()

        assertTrue(h.sent("remote.action.play").isEmpty())
        assertEquals(RemoteResultKind.FAILED, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("离线"))
    }

    @Test
    fun `ack ok shows accepted not done`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        val requestId = h.sent("remote.action.play").single().string("request_id")

        h.push(ack(requestId, ok = true, actionId = "a1"))
        main.scheduler.runCurrent()

        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertEquals("已受理：点头", h.vm.state.value.result?.text)
        // ack 只表示受理：动作仍在执行，返回键仍需拦截。
        assertEquals("点头", h.vm.state.value.sendingAction)
    }

    @Test
    fun `action result completes the matched command only once`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()

        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)
        assertEquals("点头 完成", h.vm.state.value.result?.text)
        assertNull(h.vm.state.value.sendingAction)

        // 同一 action_id 的迟到/重复结果不得覆盖第一次终态。
        h.push(actionResult("a1", "failed", detail = "servo_error"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)
        assertEquals("点头 完成", h.vm.state.value.result?.text)
    }

    @Test
    fun `action result from llm is not reported as your action`() = runTest {
        val h = harness()

        h.push(actionResult("llm-1", "done", source = "llm"))
        main.scheduler.runCurrent()

        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("机器人动作"))
        assertNull(h.vm.state.value.sendingAction)
    }

    @Test
    fun `ack timeout reports unconfirmed`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()

        main.scheduler.advanceTimeBy(5_000)
        main.scheduler.runCurrent()

        assertEquals(RemoteResultKind.FAILED, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("未确认"))
        assertNull(h.vm.state.value.sendingAction)
    }

    @Test
    fun `missing action result clears in-flight instead of trapping back`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()
        assertEquals("点头", h.vm.state.value.sendingAction)

        // ack 之后结果丢失：软超时先放行返回键，硬超时后按丢失结算。
        main.scheduler.advanceTimeBy(45_000)
        main.scheduler.runCurrent()

        assertNull(h.vm.state.value.sendingAction)
        assertEquals(RemoteResultKind.TIMEOUT, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("结果未确认"))

        // 迟到结果已被结算，不再改写结果条。
        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.TIMEOUT, h.vm.state.value.result?.kind)
    }

    @Test
    fun `disconnect clears pending and shows unconfirmed`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()

        h.disconnect()
        main.scheduler.runCurrent()

        assertNull(h.vm.state.value.sendingAction)
        assertEquals(RemoteResultKind.FAILED, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("未确认"))

        // 连接已断：后续命令不得再写入旧句柄。
        val before = h.sink.sent.size
        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        assertEquals(before, h.sink.sent.size)
    }

    @Test
    fun `reconnect reloads the snapshot so a device that came online is usable`() = runTest {
        val h = harness(deviceOnline = false)
        assertFalse(h.vm.state.value.deviceOnline)
        assertEquals(0, h.sent("remote.get_status").size)

        // 断线期间设备上线：REST 快照已更新，但内存里还是旧的离线状态。
        h.repo.devices = mutableListOf(
            DeviceDto(
                id = DEVICE_ID,
                name = "客厅机器人",
                online = true,
                volume = 30,
                state = "idle",
            ),
        )
        h.disconnect()
        main.scheduler.runCurrent()
        main.scheduler.advanceTimeBy(1_000)
        main.scheduler.runCurrent()

        assertEquals(ConnectionState.Connected, h.vm.state.value.connection)
        assertTrue(h.vm.state.value.deviceOnline)
        assertEquals(30, h.vm.state.value.volume)
        // 重连后应补拉运行态（音量/角度可能已变）。
        assertEquals(1, h.sent("remote.get_status").size)
    }

    @Test
    fun `rate limited ack is shown and does not lock the panel`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(
                h.sent("remote.action.play").single().string("request_id"),
                ok = false,
                error = "rate_limited",
            ),
        )
        main.scheduler.runCurrent()

        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("太频繁"))
        assertNull(h.vm.state.value.sendingAction)

        // 面板未锁死：用户可以立刻重试。
        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        assertEquals(2, h.sent("remote.action.play").size)
    }

    @Test
    fun `device offline ack flips panel offline`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(
                h.sent("remote.action.play").single().string("request_id"),
                ok = false,
                error = "device_offline",
            ),
        )
        main.scheduler.runCurrent()

        assertFalse(h.vm.state.value.deviceOnline)
        assertEquals("设备离线", h.vm.state.value.result?.text)

        val before = h.sink.sent.size
        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        assertEquals(before, h.sink.sent.size)
    }

    @Test
    fun `device status offline clears pending and shows the reason`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()

        h.push(deviceStatus(online = false, reason = "device_revoked"))
        main.scheduler.runCurrent()

        assertFalse(h.vm.state.value.deviceOnline)
        assertTrue(h.vm.state.value.offlineReason.contains("撤销"))
        assertNull(h.vm.state.value.sendingAction)
        assertEquals(RemoteResultKind.FAILED, h.vm.state.value.result?.kind)
    }

    @Test
    fun `status report fills volume and marks the device online`() = runTest {
        val h = harness(deviceVolume = 40)

        assertEquals(1, h.sent("remote.get_status").size)

        h.push(report(volume = 25, pan = 12.0, tilt = -5.0))
        main.scheduler.runCurrent()

        assertEquals(25, h.vm.state.value.volume)
        assertEquals(12.0, h.vm.state.value.pan, 1e-9)
        assertEquals(-5.0, h.vm.state.value.tilt, 1e-9)
        assertTrue(h.vm.state.value.deviceOnline)
    }

    @Test
    fun `calibration report does not overwrite unsaved slider draft`() = runTest {
        val h = harness()

        h.vm.previewCalibration(panOffset = 5.0, tiltOffset = -3.0)
        main.scheduler.advanceTimeBy(200)
        main.scheduler.runCurrent()
        assertEquals(1, h.sent("remote.calibrate.preview").size)

        h.push(report(panOffset = 0.0, tiltOffset = 0.0))
        main.scheduler.runCurrent()

        assertEquals(5.0, h.vm.state.value.offsetPan, 1e-9)
        assertEquals(-3.0, h.vm.state.value.offsetTilt, 1e-9)
    }

    @Test
    fun `cancelling calibration restores persisted offsets and previews them`() = runTest {
        val h = harness(panOffset = 4.0, tiltOffset = -2.0)

        h.vm.previewCalibration(panOffset = 12.0, tiltOffset = 9.0)
        main.scheduler.advanceTimeBy(200)
        main.scheduler.runCurrent()
        assertEquals(12.0, h.vm.state.value.offsetPan, 1e-9)

        h.vm.cancelCalibration()
        main.scheduler.runCurrent()

        assertEquals(4.0, h.vm.state.value.offsetPan, 1e-9)
        assertEquals(-2.0, h.vm.state.value.offsetTilt, 1e-9)
        // 取消后要再预览一次已持久化的偏移，否则云台会停在没有保存的位置。
        val preview = h.sent("remote.calibrate.preview").last()
        assertEquals(4.0, preview.double("pan_offset"), 1e-9)
        assertEquals(-2.0, preview.double("tilt_offset"), 1e-9)
    }

    @Test
    fun `saved calibration survives the stale report until result`() = runTest {
        val h = harness(panOffset = 4.0, tiltOffset = -2.0)

        h.vm.saveCalibration(panOffset = 6.0, tiltOffset = -4.0)
        main.scheduler.runCurrent()
        val requestId = h.sent("remote.calibrate.save").single().string("request_id")

        // 保存的在途阶段仍报旧偏移：不得把草稿拽回去。
        h.push(report(panOffset = 4.0, tiltOffset = -2.0))
        main.scheduler.runCurrent()
        assertEquals(6.0, h.vm.state.value.offsetPan, 1e-9)
        assertEquals(-4.0, h.vm.state.value.offsetTilt, 1e-9)

        h.push(ack(requestId, ok = true, actionId = "cal-1"))
        main.scheduler.runCurrent()
        h.push(actionResult("cal-1", "done"))
        main.scheduler.runCurrent()

        // 保存完成后恢复跟随设备上报。
        h.push(report(panOffset = 7.0, tiltOffset = -5.0))
        main.scheduler.runCurrent()
        assertEquals(7.0, h.vm.state.value.offsetPan, 1e-9)
        assertEquals(-5.0, h.vm.state.value.offsetTilt, 1e-9)
        assertEquals(7.0, h.vm.state.value.savedOffsetPan, 1e-9)
        assertEquals(-5.0, h.vm.state.value.savedOffsetTilt, 1e-9)
    }

    @Test
    fun `emergency stop clears awaiting results`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()

        h.vm.stopAll()
        main.scheduler.runCurrent()
        assertEquals(1, h.sent("remote.action.stop").size)
        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("急停"))
        assertNull(h.vm.state.value.sendingAction)

        // 服务端会把在途动作结算为 stopped；迟到的 done 不得把急停改写成“完成”。
        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("急停"))
    }

    @Test
    fun `stop before ack suppresses the late acceptance`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        val requestId = h.sent("remote.action.play").single().string("request_id")

        h.vm.stopAll()
        main.scheduler.runCurrent()

        h.push(ack(requestId, ok = true, actionId = "a1"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("急停"))

        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        assertFalse(h.vm.state.value.result?.text.orEmpty().contains("完成"))
    }

    @Test
    fun `stop before ack survives the ack timeout`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.vm.stopAll()
        main.scheduler.runCurrent()

        // 被急停作废的 ack 超时不得把“已急停”改写成“未确认”。
        main.scheduler.advanceTimeBy(5_000)
        main.scheduler.runCurrent()

        assertEquals(RemoteResultKind.INFO, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("急停"))
        assertNull(h.vm.state.value.sendingAction)
    }

    @Test
    fun `stop cancels a pending debounce so no servo command follows`() = runTest {
        val h = harness()

        h.vm.onPanTiltChange(pan = 10.0, tilt = 5.0)
        h.vm.stopAll()
        main.scheduler.runCurrent()

        main.scheduler.advanceTimeBy(1_000)
        main.scheduler.runCurrent()

        assertTrue(h.sent("remote.servo.move").isEmpty())
        assertEquals(1, h.sent("remote.action.stop").size)
    }

    @Test
    fun `late action result after the ui timeout still updates the bar`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()
        assertEquals("点头", h.vm.state.value.sendingAction)

        // 服务端排队可能超过 UI 软超时：先放行返回键，但结果仍可能到达。
        main.scheduler.advanceTimeBy(15_000)
        main.scheduler.runCurrent()
        assertNull(h.vm.state.value.sendingAction)
        assertEquals(RemoteResultKind.TIMEOUT, h.vm.state.value.result?.kind)

        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("完成"))
    }

    @Test
    fun `result before ack does not install a stuck pending`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        val requestId = h.sent("remote.action.play").single().string("request_id")

        // 结果先于 ack 到达：ack 迟到时不能再挂一个永远不会结算的 pending，
        // 而且终态必须补显，否则动作的完成/失败永远看不到。
        h.push(actionResult("a1", "done"))
        main.scheduler.runCurrent()
        h.push(ack(requestId, ok = true, actionId = "a1"))
        main.scheduler.runCurrent()

        assertNull(h.vm.state.value.sendingAction)
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("完成"))
    }

    @Test
    fun `cancelled debounce lets a later report take over again`() = runTest {
        val h = harness()

        h.vm.onPanTiltChange(pan = 10.0, tilt = 5.0)
        // 急停取消尚未发出的云台命令：草稿再也发不出去，脏标记必须复位，
        // 否则急停归位后的上报会被永久忽略，滑条停在从未下发的值上。
        h.vm.stopAll()
        main.scheduler.runCurrent()
        assertTrue(h.sent("remote.servo.move").isEmpty())

        h.push(report(pan = 42.0, tilt = 7.0))
        main.scheduler.runCurrent()
        assertEquals(42.0, h.vm.state.value.pan, 1e-9)
        assertEquals(7.0, h.vm.state.value.tilt, 1e-9)
    }

    @Test
    fun `old hard timeout does not overwrite a newer action result`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()

        // 软超时放行返回键后，用户又发起一次动作并拿到终态。
        main.scheduler.advanceTimeBy(15_000)
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.TIMEOUT, h.vm.state.value.result?.kind)

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").last().string("request_id"), ok = true, actionId = "a2"),
        )
        main.scheduler.runCurrent()
        h.push(actionResult("a2", "done"))
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)

        // 第一个动作的 45s 硬超时此时才到：不得把新动作的“完成”改回“结果未确认”。
        main.scheduler.advanceTimeBy(30_000)
        main.scheduler.runCurrent()
        assertEquals(RemoteResultKind.DONE, h.vm.state.value.result?.kind)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("完成"))
    }

    @Test
    fun `suppressed old action still releases the back lock`() = runTest {
        val h = harness()

        // A：动作已受理，结果迟迟不来（服务端允许排队到 30s）。
        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()

        // B：校准保存不检查发送锁，先完成。
        h.vm.saveCalibration(panOffset = 6.0, tiltOffset = -4.0)
        main.scheduler.runCurrent()
        h.push(
            ack(
                h.sent("remote.calibrate.save").single().string("request_id"),
                ok = true,
                actionId = "cal-1",
            ),
        )
        main.scheduler.runCurrent()
        h.push(actionResult("cal-1", "done"))
        main.scheduler.runCurrent()

        // A 的软超时到：文案让位给 B，但 A 必须从“执行中”摘除，否则返回键永久被锁。
        main.scheduler.advanceTimeBy(15_000)
        main.scheduler.runCurrent()
        assertNull(h.vm.state.value.sendingAction)
        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("完成"))
    }

    @Test
    fun `early calibration result still unfreezes the draft`() = runTest {
        val h = harness(panOffset = 4.0, tiltOffset = -2.0)

        h.vm.saveCalibration(panOffset = 6.0, tiltOffset = -4.0)
        main.scheduler.runCurrent()
        val requestId = h.sent("remote.calibrate.save").single().string("request_id")

        // 结果先于 ack 时补显终态，也必须执行“解冻草稿”的副作用。
        h.push(actionResult("cal-1", "done"))
        main.scheduler.runCurrent()
        h.push(ack(requestId, ok = true, actionId = "cal-1"))
        main.scheduler.runCurrent()

        h.push(report(panOffset = 9.0, tiltOffset = 5.0))
        main.scheduler.runCurrent()
        assertEquals(9.0, h.vm.state.value.savedOffsetPan, 1e-9)
        assertEquals(5.0, h.vm.state.value.savedOffsetTilt, 1e-9)
    }

    @Test
    fun `calibration draft freezes the persisted offsets until save`() = runTest {
        val h = harness(panOffset = 4.0, tiltOffset = -2.0)

        h.vm.previewCalibration(panOffset = 10.0, tiltOffset = 6.0)
        main.scheduler.advanceTimeBy(200)
        main.scheduler.runCurrent()

        // 草稿期间设备回报的是旧持久化值：不得写进 savedOffset，
        // 否则「取消校准」会把未保存的偏移当成已保存值恢复。
        h.push(report(panOffset = 3.0, tiltOffset = 1.0))
        main.scheduler.runCurrent()
        assertEquals(4.0, h.vm.state.value.savedOffsetPan, 1e-9)
        assertEquals(-2.0, h.vm.state.value.savedOffsetTilt, 1e-9)

        h.vm.cancelCalibration()
        main.scheduler.runCurrent()
        val preview = h.sent("remote.calibrate.preview").last()
        assertEquals(4.0, preview.double("pan_offset"), 1e-9)
        assertEquals(-2.0, preview.double("tilt_offset"), 1e-9)
    }

    @Test
    fun `server status snapshot does not mark an offline device online`() = runTest {
        val h = harness(deviceOnline = false)

        h.push(report(volume = 20, source = "server"))
        main.scheduler.runCurrent()

        assertFalse(h.vm.state.value.deviceOnline)
        assertEquals(20, h.vm.state.value.volume)
    }

    @Test
    fun `timeout detail is shown instead of the generic failure text`() = runTest {
        val h = harness()

        h.vm.playAction("nod")
        main.scheduler.runCurrent()
        h.push(
            ack(h.sent("remote.action.play").single().string("request_id"), ok = true, actionId = "a1"),
        )
        main.scheduler.runCurrent()
        h.push(actionResult("a1", "failed", detail = "timeout"))
        main.scheduler.runCurrent()

        assertTrue(h.vm.state.value.result?.text.orEmpty().contains("超时"))
    }

    @Test
    fun `failed status query does not wedge later refreshes`() = runTest {
        val h = harness()
        val requestId = h.sent("remote.get_status").single().string("request_id")

        h.push(ack(requestId, ok = false, error = "device_offline"))
        main.scheduler.runCurrent()
        assertFalse(h.vm.state.value.deviceOnline)

        // 设备恢复在线后应能重新拉状态，而不是被上一次失败的“在途”标记卡死。
        h.push(deviceStatus(online = true, reason = ""))
        main.scheduler.runCurrent()

        assertTrue(h.vm.state.value.deviceOnline)
        assertEquals(2, h.sent("remote.get_status").size)
    }

    @Test
    fun `catalog loads actions and limits`() = runTest {
        val h = harness()

        assertEquals(listOf("nod"), h.vm.state.value.actions.map { it.name })
        assertEquals(mapOf("basic" to "基础"), h.vm.state.value.categories)
        assertEquals(1, h.repo.catalogCalls)

        h.repo.catalogError = me.rerere.companion.core.AppError.Network("目录加载失败")
        h.vm.refreshCatalog()
        main.scheduler.runCurrent()
        assertEquals("目录加载失败", h.vm.state.value.catalogError)
        // 失败不清空已有目录：用户仍可点已加载的动作。
        assertEquals(listOf("nod"), h.vm.state.value.actions.map { it.name })
    }

    companion object {
        private const val DEVICE_ID = "d1"
    }
}

// 顶层：`Harness` 是嵌套类，不能使用外层测试类的成员扩展函数。
private fun JsonObject.string(key: String): String = getValue(key).jsonPrimitive.content

private fun JsonObject.int(key: String): Int = getValue(key).jsonPrimitive.int

private fun JsonObject.double(key: String): Double = getValue(key).jsonPrimitive.double
