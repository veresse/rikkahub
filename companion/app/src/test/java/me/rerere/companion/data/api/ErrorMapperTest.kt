package me.rerere.companion.data.api

import me.rerere.companion.core.AppError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ErrorMapperTest {

    @Test
    fun `maps known detail code to chinese message`() {
        val error = ErrorMapper.map(401, """{"detail":"invalid_credentials"}""")
        assertEquals("invalid_credentials", error.code)
        assertEquals("账号或口令错误", error.message)
    }

    @Test
    fun `keeps unknown chinese detail as message`() {
        val error = ErrorMapper.map(400, """{"detail":"服务器开小差了"}""")
        assertEquals("服务器开小差了", error.message)
    }

    @Test
    fun `maps validation error array`() {
        val error = ErrorMapper.map(422, """{"detail":[{"msg":"field required"}]}""")
        assertEquals("validation_error", error.code)
        assertTrue(error.message.isNotBlank())
    }

    @Test
    fun `falls back to status message when body is missing`() {
        val error = ErrorMapper.map(500, null)
        assertTrue(error.message.contains("500"))
    }

    @Test
    fun `prefers explicit message field`() {
        val error = ErrorMapper.map(400, """{"detail":"x","message":"可读说明"}""")
        assertEquals("可读说明", error.message)
    }

    @Test
    fun `parses retry after header`() {
        assertEquals(120, ErrorMapper.retryAfterSeconds("120"))
        assertEquals(0, ErrorMapper.retryAfterSeconds("0"))
        assertEquals(0, ErrorMapper.retryAfterSeconds("-3"))
        assertNull(ErrorMapper.retryAfterSeconds(null))
        assertNull(ErrorMapper.retryAfterSeconds("abc"))
    }

    @Test
    fun `unknown english detail does not leak internal code`() {
        val error = ErrorMapper.map(400, """{"detail":"some_internal_code"}""")
        assertEquals("some_internal_code", error.code)
        assertEquals("请求失败（400）", error.message)
    }

    @Test
    fun `mixed internal text in detail is not shown`() {
        val detail = "ValueError: 参数非法"
        val error = ErrorMapper.map(400, """{"detail":"$detail"}""")
        assertEquals(detail, error.code)
        assertEquals("请求失败（400）", error.message)
    }

    @Test
    fun `server error message is not trusted`() {
        val error = ErrorMapper.map(
            500,
            """{"detail":"boom","message":"Traceback: secret path /opt/app"}""",
        )
        assertEquals("boom", error.code)
        assertFalse(error.message.contains("Traceback"))
        assertTrue(error.message.contains("500"))
    }

    @Test
    fun `carries retry after into rate limited error`() {
        val error = ErrorMapper.map(429, """{"detail":"rate_limited"}""", retryAfter = "42") as AppError.Http
        assertEquals(42, error.retryAfterSeconds)
    }
}
