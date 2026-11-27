package me.rerere.companion.core

import org.junit.Assert.assertEquals
import org.junit.Test

class UrlValidatorTest {

    @Test
    fun `adds trailing slash to bare https host`() {
        assertEquals(
            UrlCheck.Ok("https://example.com/"),
            UrlValidator.validate("https://example.com", allowCleartext = false),
        )
    }

    @Test
    fun `keeps port and path prefix`() {
        assertEquals(
            UrlCheck.Ok("https://example.com:8443/companion/"),
            UrlValidator.validate("https://example.com:8443/companion", allowCleartext = false),
        )
    }

    @Test
    fun `trims surrounding whitespace`() {
        assertEquals(
            UrlCheck.Ok("https://example.com/"),
            UrlValidator.validate("  https://example.com  ", allowCleartext = false),
        )
    }

    @Test
    fun `rejects cleartext for remote host even when cleartext flag is on`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.CLEARTEXT_NOT_ALLOWED),
            UrlValidator.validate("http://example.com", allowCleartext = true),
        )
    }

    @Test
    fun `allows cleartext localhost only in debug`() {
        assertEquals(
            UrlCheck.Ok("http://localhost:8000/"),
            UrlValidator.validate("http://localhost:8000", allowCleartext = true),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.CLEARTEXT_NOT_ALLOWED),
            UrlValidator.validate("http://localhost:8000", allowCleartext = false),
        )
    }

    @Test
    fun `allows emulator host in debug`() {
        assertEquals(
            UrlCheck.Ok("http://10.0.2.2:8000/"),
            UrlValidator.validate("http://10.0.2.2:8000/", allowCleartext = true),
        )
    }

    @Test
    fun `rejects non http scheme`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.BAD_SCHEME),
            UrlValidator.validate("ftp://example.com", allowCleartext = true),
        )
    }

    @Test
    fun `rejects userinfo`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.HAS_USERINFO),
            UrlValidator.validate("https://user:pass@example.com", allowCleartext = false),
        )
    }

    @Test
    fun `rejects query and fragment`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.HAS_QUERY_OR_FRAGMENT),
            UrlValidator.validate("https://example.com/?a=1", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.HAS_QUERY_OR_FRAGMENT),
            UrlValidator.validate("https://example.com/#x", allowCleartext = false),
        )
    }

    @Test
    fun `rejects missing host`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_HOST),
            UrlValidator.validate("https://", allowCleartext = false),
        )
    }

    @Test
    fun `rejects blank input`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.EMPTY),
            UrlValidator.validate("   ", allowCleartext = false),
        )
    }

    @Test
    fun `rejects port outside valid range`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PORT),
            UrlValidator.validate("https://example.com:0", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PORT),
            UrlValidator.validate("https://example.com:65536", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PORT),
            UrlValidator.validate("https://example.com:99999", allowCleartext = false),
        )
    }

    @Test
    fun `accepts max port`() {
        assertEquals(
            UrlCheck.Ok("https://example.com:65535/"),
            UrlValidator.validate("https://example.com:65535", allowCleartext = false),
        )
    }

    @Test
    fun `rejects dot segments`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PATH),
            UrlValidator.validate("https://example.com/companion/../admin", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PATH),
            UrlValidator.validate("https://example.com/./x", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_PATH),
            UrlValidator.validate("https://example.com/%2e%2e/admin", allowCleartext = false),
        )
    }

    @Test
    fun `normalizes uppercase host and trailing dot`() {
        assertEquals(
            UrlCheck.Ok("https://example.com/"),
            UrlValidator.validate("HTTPS://Example.COM", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Ok("https://example.com/"),
            UrlValidator.validate("https://example.com.", allowCleartext = false),
        )
    }

    @Test
    fun `allows cleartext uppercase localhost in debug`() {
        assertEquals(
            UrlCheck.Ok("http://localhost:8000/"),
            UrlValidator.validate("HTTP://LOCALHOST:8000", allowCleartext = true),
        )
    }

    @Test
    fun `keeps ipv6 literal bracketed`() {
        assertEquals(
            UrlCheck.Ok("https://[::1]/"),
            UrlValidator.validate("https://[::1]", allowCleartext = false),
        )
    }

    @Test
    fun `rejects backslash in address`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_HOST),
            UrlValidator.validate("https://example.com\\evil", allowCleartext = false),
        )
    }

    @Test
    fun `rejects ipv4 literal with out of range octet`() {
        assertEquals(
            UrlCheck.Invalid(UrlError.INVALID_HOST),
            UrlValidator.validate("https://999.999.999.999", allowCleartext = false),
        )
        assertEquals(
            UrlCheck.Ok("https://10.0.0.1/"),
            UrlValidator.validate("https://10.0.0.1", allowCleartext = false),
        )
    }

    @Test
    fun `accepts ipv4 mapped ipv6 literal`() {
        assertEquals(
            UrlCheck.Ok("https://[::ffff:127.0.0.1]/"),
            UrlValidator.validate("https://[::ffff:127.0.0.1]", allowCleartext = false),
        )
    }
}
