package me.rerere.companion.data.api

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import me.rerere.companion.core.AppError

object ErrorMapper {

    private val messages = mapOf(
        "invalid_credentials" to "账号或口令错误",
        "rate_limited" to "尝试过于频繁，请稍后再试",
        "admin_password_not_configured" to "服务端未配置管理员口令",
        "token_revoked" to "登录已失效，请重新登录",
        "device_offline" to "设备不在线",
        "queue_full" to "设备指令队列已满",
        "angle_out_of_range" to "角度超出范围",
        "preview_expired" to "预览已过期，请重新选择文件",
    )

    fun map(status: Int, body: String?, retryAfter: String? = null): AppError {
        val retrySeconds = retryAfterSeconds(retryAfter)
        val payload = body?.let { raw ->
            runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
        }

        val explicit = payload?.get("message")?.let { element ->
            runCatching { element.jsonPrimitive.contentOrNull }.getOrNull()
        }
        if (!explicit.isNullOrBlank()) {
            return AppError.Http(codeOf(payload, status), explicit, retrySeconds)
        }

        when (val detail = payload?.get("detail")) {
            is JsonPrimitive -> if (detail.isString) {
                val code = detail.content
                return AppError.Http(code, messages[code] ?: code, retrySeconds)
            }

            is JsonArray -> return AppError.Http("validation_error", "请求参数不合法", retrySeconds)
            else -> Unit
        }

        return AppError.Http(codeOf(payload, status), defaultMessage(status), retrySeconds)
    }

    fun retryAfterSeconds(header: String?): Int? {
        val value = header?.trim()?.toIntOrNull() ?: return null
        return value.coerceAtLeast(0)
    }

    private fun codeOf(payload: JsonObject?, status: Int): String {
        val detail = payload?.get("detail")
        if (detail is JsonPrimitive && detail.isString) return detail.content
        return "http_$status"
    }

    private fun defaultMessage(status: Int): String = when (status) {
        401 -> "登录已失效，请重新登录"
        403 -> "没有权限执行该操作"
        404 -> "请求的资源不存在"
        429 -> "尝试过于频繁，请稍后再试"
        in 500..599 -> "服务器开小差了（$status）"
        else -> "请求失败（$status）"
    }
}
