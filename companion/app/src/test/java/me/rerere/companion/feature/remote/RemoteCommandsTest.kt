package me.rerere.companion.feature.remote

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** `remote.*` 命令构造的纯函数测试：逐字段断言，不依赖 Android 与网络。 */
class RemoteCommandsTest {

    private fun command(text: String): JsonObject = Json.parseToJsonElement(text).jsonObject

    private fun JsonObject.string(key: String): String = getValue(key).jsonPrimitive.content

    private fun JsonObject.double(key: String): Double = getValue(key).jsonPrimitive.double

    private fun JsonObject.int(key: String): Int = getValue(key).jsonPrimitive.int

    private fun JsonObject.bool(key: String): Boolean = getValue(key).jsonPrimitive.boolean

    @Test
    fun `every command carries the protocol envelope`() {
        val all = listOf(
            remoteGetStatus(requestId = "r1", deviceId = "d1", ts = 7),
            remoteActionPlay("r2", "d1", "nod", ts = 7),
            remoteServoMove("r3", "d1", 1.0, 2.0, ts = 7),
            remoteVolumeSet("r4", "d1", 50, ts = 7),
            remoteIdleSet("r5", "d1", true, ts = 7),
            remoteActionStop("r6", "d1", ts = 7),
            remoteCalibratePreview("r7", "d1", 1.0, 2.0, ts = 7),
            remoteCalibrateSave("r8", "d1", 1.0, 2.0, ts = 7),
        )
        all.forEach { text ->
            val body = command(text)
            assertEquals(2, body.int("v"))
            assertEquals("d1", body.string("device_id"))
            assertEquals(7L, body.string("ts").toLong())
            assertTrue(body.string("request_id").startsWith("r"))
            assertTrue(body.string("type").startsWith("remote."))
        }
    }

    @Test
    fun `servo move clamps to protocol limits`() {
        val body = command(
            remoteServoMove(requestId = "r1", deviceId = "d1", pan = -120.0, tilt = 90.0),
        )

        assertEquals("remote.servo.move", body.string("type"))
        assertEquals(-90.0, body.double("pan"), 1e-9)
        assertEquals(45.0, body.double("tilt"), 1e-9)
        assertEquals(500, body.int("speed"))
    }

    @Test
    fun `volume and action params are clamped`() {
        assertEquals(
            100,
            command(remoteVolumeSet("r1", "d1", 120)).int("volume"),
        )
        assertEquals(
            0,
            command(remoteVolumeSet("r2", "d1", -5)).int("volume"),
        )

        val body = command(
            remoteActionPlay(
                requestId = "r3",
                deviceId = "d1",
                name = "nod",
                intensity = 9,
                speed = 0,
                repeat = 42,
            ),
        )
        assertEquals("remote.action.play", body.string("type"))
        assertEquals("nod", body.string("name"))
        assertEquals(3, body.int("intensity"))
        assertEquals(1, body.int("speed"))
        assertEquals(5, body.int("repeat"))
    }

    @Test
    fun `action play carries selected params`() {
        val body = command(
            remoteActionPlay("r1", "d1", "nod", intensity = 3, speed = 1, repeat = 2),
        )

        assertEquals(3, body.int("intensity"))
        assertEquals(1, body.int("speed"))
        assertEquals(2, body.int("repeat"))
    }

    @Test
    fun `calibration clamps offsets to 30`() {
        val preview = command(remoteCalibratePreview("r1", "d1", 45.0, -31.0))
        assertEquals("remote.calibrate.preview", preview.string("type"))
        assertEquals(30.0, preview.double("pan_offset"), 1e-9)
        assertEquals(-30.0, preview.double("tilt_offset"), 1e-9)

        val save = command(remoteCalibrateSave("r2", "d1", -45.0, 31.0))
        assertEquals("remote.calibrate.save", save.string("type"))
        assertEquals(-30.0, save.double("pan_offset"), 1e-9)
        assertEquals(30.0, save.double("tilt_offset"), 1e-9)
    }

    @Test
    fun `stop and idle commands carry their payload`() {
        val stop = command(remoteActionStop("r1", "d1", reason = "barge_in"))
        assertEquals("remote.action.stop", stop.string("type"))
        assertEquals("barge_in", stop.string("reason"))
        // 协议 §4.6：action.stop 自身不带 action_id。
        assertTrue(stop["action_id"] == null)

        val idle = command(remoteIdleSet("r2", "d1", enabled = false))
        assertEquals("remote.idle.set", idle.string("type"))
        assertEquals(false, idle.bool("enabled"))
    }

    @Test
    fun `ack errors become readable chinese`() {
        assertEquals("设备离线", remoteAckError("device_offline"))
        assertEquals("操作太频繁，请稍后再试", remoteAckError("rate_limited"))
        assertEquals("设备固件过旧，不支持遥控", remoteAckError("unsupported_protocol"))
        assertEquals("机器人正忙，请稍后再试", remoteAckError("queue_full"))
        assertEquals("角度超出范围", remoteAckError("angle_out_of_range"))
        assertEquals("舵机执行失败", remoteAckError("servo_error"))
        assertEquals("超时", remoteAckError("timeout"))
        // 未知/空 code 不得把英文错误码直接吐到 UI 上。
        assertEquals("遥控指令失败", remoteAckError("weird_code"))
        assertEquals("遥控指令失败", remoteAckError(null))
    }

    @Test
    fun `offline reasons become readable chinese`() {
        assertEquals("设备已离线", deviceOfflineReason(""))
        assertEquals("设备已离线", deviceOfflineReason(null))
        assertEquals("设备已撤销，需恢复出厂后重新配对", deviceOfflineReason("device_revoked"))
        assertEquals("服务端正在重启", deviceOfflineReason("server_shutdown"))
        assertEquals("设备握手失败", deviceOfflineReason("handshake_failed"))
        assertEquals("服务端内部错误", deviceOfflineReason("internal_error"))
        assertEquals("设备已离线", deviceOfflineReason("something_else"))
    }
}
