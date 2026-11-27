package me.rerere.companion.data.sse

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SseParserTest {

    @Test
    fun `parses content and done across split chunks`() {
        val parser = SseParser()
        val full = buildString {
            append("data: {\"type\":\"content\",\"delta\":\"你\"}\n\n")
            append("data: {\"type\":\"done\",\"finish_reason\":\"stop\",")
            append("\"assistant_message_id\":\"m1\",\"user_message_id\":\"u1\",\"replayed\":false}\n\n")
        }
        val events = mutableListOf<ChatEvent>()
        // 故意把一行、一个多字节字符都切断。
        events += parser.feed(full.substring(0, 9))
        events += parser.feed(full.substring(9, 25))
        events += parser.feed(full.substring(25, full.length - 1))
        events += parser.feed(full.substring(full.length - 1))
        events += parser.finish()

        assertEquals(2, events.size)
        assertEquals(ChatEvent.Content("你"), events[0])
        val done = events[1] as ChatEvent.Done
        assertEquals("stop", done.finishReason)
        assertEquals("m1", done.assistantMessageId)
        assertEquals("u1", done.userMessageId)
        assertEquals(false, done.replayed)
    }

    @Test
    fun `handles crlf and multiline data`() {
        val parser = SseParser()
        val events = parser.feed(
            "data: {\"type\":\"status\",\r\n" +
                "data: \"status\":\"generating\"}\r\n\r\n",
        )

        assertEquals(1, events.size)
        assertEquals(ChatEvent.Status("generating"), events.single())
    }

    @Test
    fun `ignores comments and bad json`() {
        val parser = SseParser()
        val events = parser.feed(
            ": keep-alive\n\n" +
                "data: not-json\n\n" +
                "data: {\"type\":\"unknown_type\"}\n\n" +
                "data: {\"type\":\"content\",\"delta\":\"ok\"}\n\n",
        )

        assertEquals(listOf(ChatEvent.Content("ok")), events)
    }

    @Test
    fun `maps error string and replayed done`() {
        val parser = SseParser()
        val events = parser.feed(
            "data: {\"type\":\"error\",\"error\":\"duplicate_in_progress\"}\n\n" +
                "data: {\"type\":\"done\",\"replayed\":true}\n\n",
        )

        val failed = events[0] as ChatEvent.Failed
        assertEquals("duplicate_in_progress", failed.code)
        assertTrue(failed.message.contains("生成中"))
        val done = events[1] as ChatEvent.Done
        assertEquals("stop", done.finishReason)
        assertEquals(true, done.replayed)
    }

    @Test
    fun `unknown error text is not shown verbatim`() {
        val parser = SseParser()
        val event = parser.feed(
            "data: {\"type\":\"error\",\"error\":\"Traceback: internal detail\"}\n\n",
        ).single() as ChatEvent.Failed

        assertEquals("chat_error", event.code)
        assertEquals("生成失败，请稍后重试", event.message)
    }
}
