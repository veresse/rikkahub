package me.rerere.companion.data.sse

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * 增量 SSE 解析器：按行缓冲，兼容网络块把一行或一个多字节字符切断。
 *
 * 只理解 `data:` 行；`:` 注释、空 `data`、坏 JSON、未知类型全部丢弃。
 * 多行 `data` 按 SSE 规范用 `\n` 拼接。
 */
class SseParser(
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private val buffer = StringBuilder()
    private val dataLines = mutableListOf<String>()

    fun feed(chunk: String): List<ChatEvent> {
        buffer.append(chunk)
        val events = mutableListOf<ChatEvent>()
        while (true) {
            val newline = buffer.indexOf("\n")
            if (newline < 0) break
            val raw = buffer.substring(0, newline)
            buffer.delete(0, newline + 1)
            handleLine(raw.removeSuffix("\r"), events)
        }
        return events
    }

    /** 流结束时调用：处理没有以空行结尾的最后一帧。 */
    fun finish(): List<ChatEvent> {
        val events = mutableListOf<ChatEvent>()
        if (buffer.isNotEmpty()) {
            val raw = buffer.toString()
            buffer.setLength(0)
            handleLine(raw.removeSuffix("\r"), events)
        }
        flush(events)
        return events
    }

    private fun handleLine(line: String, events: MutableList<ChatEvent>) {
        when {
            line.isEmpty() -> flush(events)
            line.startsWith(":") -> Unit
            line.startsWith("data:") -> {
                val value = line.removePrefix("data:")
                dataLines.add(value.removePrefix(" "))
            }
        }
    }

    private fun flush(events: MutableList<ChatEvent>) {
        if (dataLines.isEmpty()) return
        val payload = dataLines.joinToString("\n")
        dataLines.clear()
        val obj = runCatching {
            json.parseToJsonElement(payload) as? JsonObject
        }.getOrNull() ?: return
        parse(obj)?.let(events::add)
    }

    private fun parse(obj: JsonObject): ChatEvent? {
        val type = obj.string("type") ?: return null
        return when (type) {
            "content" -> ChatEvent.Content(obj.string("delta") ?: "")
            "reasoning" -> ChatEvent.Reasoning(obj.string("delta") ?: "")
            "status" -> ChatEvent.Status(
                status = obj.string("status") ?: "unknown",
                message = obj.string("message"),
            )
            "tool_result" -> ChatEvent.ToolResult(
                tool = obj.string("tool"),
                resultJson = obj["result"]?.toString() ?: "{}",
            )
            "usage" -> ChatEvent.Usage(obj["usage"]?.toString() ?: "{}")
            "error" -> {
                // 服务端可能把 Python/上游供应商异常原文塞进 error；客户端只展示
                // 已知可读原因，未知一律通用文案，避免把内部信息丢给用户。
                val raw = obj.string("error") ?: "生成失败"
                val message = when (raw) {
                    "duplicate_in_progress" -> "上一条还在生成中，请稍候"
                    "timeout" -> "生成超时，请重试"
                    "interrupted" -> "生成已中断"
                    "model_error" -> "模型服务出错，请稍后重试"
                    else -> "生成失败，请稍后重试"
                }
                ChatEvent.Failed(
                    code = if (raw == "duplicate_in_progress") raw else "chat_error",
                    message = message,
                )
            }
            "done" -> ChatEvent.Done(
                finishReason = obj.string("finish_reason") ?: "stop",
                assistantMessageId = obj.string("assistant_message_id"),
                userMessageId = obj.string("user_message_id"),
                replayed = obj.boolean("replayed") ?: false,
            )
            else -> null
        }
    }

    private fun JsonObject.string(key: String): String? =
        (this[key] as? JsonPrimitive)?.contentOrNull

    private fun JsonObject.boolean(key: String): Boolean? =
        (this[key] as? JsonPrimitive)?.booleanOrNull
}
