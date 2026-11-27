package me.rerere.companion.feature.remote

import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * App → 服务端的 `remote.*` 命令构造（协议 §8.1）。
 *
 * 只做纯字符串拼装与限幅：不依赖 Android、不读网络。所有函数都返回可直接
 * 交给 `AppWsManager.send` 的 JSON 文本，方便单测逐字段断言。
 */

/** 统一信封：`v/type/request_id/device_id/ts`。 */
private fun remoteEnvelope(
    type: String,
    requestId: String,
    deviceId: String,
    ts: Long,
    body: JsonObjectBuilder.() -> Unit = {},
): String = buildJsonObject {
    put("v", 2)
    put("type", type)
    put("request_id", requestId)
    put("device_id", deviceId)
    put("ts", ts)
    body()
}.toString()

fun remoteGetStatus(
    requestId: String,
    deviceId: String,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.get_status", requestId, deviceId, ts)

fun remoteActionPlay(
    requestId: String,
    deviceId: String,
    name: String,
    intensity: Int = 2,
    speed: Int = 2,
    repeat: Int = 1,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.action.play", requestId, deviceId, ts) {
    put("name", name)
    put("intensity", intensity.coerceIn(1, 3))
    put("speed", speed.coerceIn(1, 3))
    put("repeat", repeat.coerceIn(1, 5))
}

/** 云台角度：`pan ∈ [-90, 90]`、`tilt ∈ [-20, 45]`、`speed ∈ [0, 1000]`。 */
fun remoteServoMove(
    requestId: String,
    deviceId: String,
    pan: Double,
    tilt: Double,
    speed: Int = 500,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.servo.move", requestId, deviceId, ts) {
    put("pan", pan.coerceIn(-90.0, 90.0))
    put("tilt", tilt.coerceIn(-20.0, 45.0))
    put("speed", speed.coerceIn(0, 1000))
}

fun remoteVolumeSet(
    requestId: String,
    deviceId: String,
    volume: Int,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.volume.set", requestId, deviceId, ts) {
    put("volume", volume.coerceIn(0, 100))
}

fun remoteIdleSet(
    requestId: String,
    deviceId: String,
    enabled: Boolean,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.idle.set", requestId, deviceId, ts) {
    put("enabled", enabled)
}

fun remoteActionStop(
    requestId: String,
    deviceId: String,
    reason: String = "user_stop",
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.action.stop", requestId, deviceId, ts) {
    put("reason", reason)
}

/** 校准偏移：协议上限 `[-30, 30]`。 */
fun remoteCalibratePreview(
    requestId: String,
    deviceId: String,
    panOffset: Double,
    tiltOffset: Double,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.calibrate.preview", requestId, deviceId, ts) {
    put("pan_offset", panOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT))
    put("tilt_offset", tiltOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT))
}

fun remoteCalibrateSave(
    requestId: String,
    deviceId: String,
    panOffset: Double,
    tiltOffset: Double,
    ts: Long = System.currentTimeMillis(),
): String = remoteEnvelope("remote.calibrate.save", requestId, deviceId, ts) {
    put("pan_offset", panOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT))
    put("tilt_offset", tiltOffset.coerceIn(-REMOTE_OFFSET_LIMIT, REMOTE_OFFSET_LIMIT))
}

/**
 * `remote.ack(error=...)` 与 `action.result(detail=...)` 的人类可读文案。
 *
 * 未知 code 一律回落到通用句子：英文错误码不直接出现在 UI 上。
 */
fun remoteAckError(code: String?): String = when (code) {
    "device_offline" -> "设备离线"
    "rate_limited" -> "操作太频繁，请稍后再试"
    "unsupported_protocol" -> "设备固件过旧，不支持遥控"
    "unknown_action" -> "未知动作"
    "invalid_arguments", "invalid_request" -> "参数不合法"
    "angle_out_of_range" -> "角度超出范围"
    "queue_full" -> "机器人正忙，请稍后再试"
    "unknown_command" -> "不支持的遥控指令"
    "internal_error" -> "服务端内部错误"
    "stopped" -> "已被停止"
    "superseded" -> "被新的指令覆盖"
    "servo_error" -> "舵机执行失败"
    "device_revoked" -> "设备已撤销"
    "timeout" -> "超时"
    else -> "遥控指令失败"
}

/**
 * `device.status.reason` 的中文文案（协议 §8.2 的封闭枚举）。
 *
 * 空字符串表示普通断线；未知值一律按“离线”展示，不把英文码吐给用户。
 */
fun deviceOfflineReason(code: String?): String = when (code) {
    null, "" -> "设备已离线"
    "device_revoked" -> "设备已撤销，需恢复出厂后重新配对"
    "server_shutdown" -> "服务端正在重启"
    "handshake_failed" -> "设备握手失败"
    "internal_error" -> "服务端内部错误"
    else -> "设备已离线"
}

/** 校准偏移的协议上限；App 滑条与命令构造共用，避免两处写死不同的值。 */
const val REMOTE_OFFSET_LIMIT = 30.0
