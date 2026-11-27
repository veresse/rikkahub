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
        "token_not_found" to "该登录已不存在或已撤销",
        "device_offline" to "设备不在线",
        "queue_full" to "设备指令队列已满",
        "angle_out_of_range" to "角度超出范围",
        "preview_expired" to "预览已过期，请重新选择文件",
        "file_too_large" to "文件超过服务器允许的大小",
        "backup_parse_failed" to "备份文件无法解析",
        "import_failed" to "导入数据失败",
        "invalid_base_url" to "服务器地址不合法",
        "invalid_secret" to "Key 格式不合法",
        "invalid_deepseek_model" to "模型名称不合法",
        "unknown_setting" to "未知的设置项",
        "invalid_voice_provider" to "不支持该语音提供方",
        "invalid_dashscope_region" to "地域不合法",
        "invalid_dashscope_asr_model" to "识别模型不合法",
        "invalid_dashscope_tts_model" to "合成模型不合法",
        "invalid_dashscope_tts_voice" to "音色不合法",
        "no_previous_package" to "没有可回滚的上一版素材",
        "invalid_package" to "素材包校验失败",
        "upload_expired" to "上传已过期，请重新选择文件",
        "asset_frame_not_found" to "素材帧不存在",
        "frame_not_found" to "素材帧不存在",
        "invalid_archive" to "素材包不是有效的 ZIP",
        "archive_too_large" to "素材包超过允许的大小",
        "extracted_too_large" to "素材包解压后超过允许的大小",
        "too_many_frames" to "素材帧数超过上限",
        "frame_too_large" to "单帧文件超过允许的大小",
        "invalid_frame" to "素材帧必须是 240×240 的 PNG",
        "invalid_manifest" to "素材包清单不合法",
        "unsafe_path" to "素材包包含不安全的路径",
        "duplicate_entry" to "素材包包含重复文件",
        "unsupported_entry" to "素材包包含不允许的文件",
        "missing_manifest" to "素材包缺少 manifest.json",
        "too_many_entries" to "素材包文件数超过上限",
    )

    fun map(status: Int, body: String?, retryAfter: String? = null): AppError {
        val retrySeconds = retryAfterSeconds(retryAfter)
        val payload = body?.let { raw ->
            runCatching { Json.parseToJsonElement(raw) as? JsonObject }.getOrNull()
        }

        val explicit = payload?.get("message")?.let { element ->
            runCatching { element.jsonPrimitive.contentOrNull }.getOrNull()
        }
        // 5xx 的 message 可能来自上游/框架，可能含内部信息，只给固定文案。
        if (!explicit.isNullOrBlank() && status < 500) {
            return AppError.Http(codeOf(payload, status), explicit, retrySeconds)
        }

        when (val detail = payload?.get("detail")) {
            is JsonPrimitive -> if (detail.isString) {
                val code = detail.content
                return AppError.Http(code, displayOf(code, status), retrySeconds)
            }

            is JsonArray -> return AppError.Http("validation_error", "请求参数不合法", retrySeconds)
            else -> Unit
        }

        return AppError.Http(codeOf(payload, status), defaultMessage(status), retrySeconds)
    }

    /**
     * 已知 code 用固定中文；旧服务端的 `detail` 是中文句子，可原样展示；
     * 其余英文 code/混合了英文标识的文案不透出（避免内部标识/异常文案进 UI）。
     */
    private fun displayOf(code: String, status: Int): String {
        if (status >= 500) return defaultMessage(status)
        messages[code]?.let { return it }
        val hasAsciiLetters = code.any { it in 'A'..'Z' || it in 'a'..'z' }
        val hasCjk = code.any { it.code > 0x7F }
        return if (hasCjk && !hasAsciiLetters) code else defaultMessage(status)
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
