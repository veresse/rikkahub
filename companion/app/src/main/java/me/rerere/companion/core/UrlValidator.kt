package me.rerere.companion.core

import java.net.URI
import java.net.URISyntaxException

enum class UrlError {
    EMPTY,
    BAD_SCHEME,
    CLEARTEXT_NOT_ALLOWED,
    INVALID_HOST,
    INVALID_PORT,
    INVALID_PATH,
    HAS_QUERY_OR_FRAGMENT,
    HAS_USERINFO,
}

sealed interface UrlCheck {
    data class Ok(val normalized: String) : UrlCheck
    data class Invalid(val reason: UrlError) : UrlCheck
}

object UrlValidator {
    private val DEBUG_CLEARTEXT_HOSTS = setOf("localhost", "127.0.0.1", "10.0.2.2")
    private val HOSTNAME = Regex("^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")
    private val IPV6 = Regex("^[0-9a-f:.]+$")

    fun validate(raw: String, allowCleartext: Boolean): UrlCheck {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return UrlCheck.Invalid(UrlError.EMPTY)
        // 反斜杠会被部分解析器当成路径分隔符，OkHttp 与 java.net.URI 处理不一致，直接拒绝。
        if (trimmed.contains('\\')) return UrlCheck.Invalid(UrlError.INVALID_HOST)

        val uri = try {
            URI(trimmed)
        } catch (_: URISyntaxException) {
            return UrlCheck.Invalid(UrlError.INVALID_HOST)
        }

        val scheme = uri.scheme?.lowercase() ?: return UrlCheck.Invalid(UrlError.BAD_SCHEME)
        if (scheme != "http" && scheme != "https") {
            return UrlCheck.Invalid(UrlError.BAD_SCHEME)
        }

        val rawHost = uri.host ?: return UrlCheck.Invalid(UrlError.INVALID_HOST)
        if (!uri.userInfo.isNullOrEmpty()) {
            return UrlCheck.Invalid(UrlError.HAS_USERINFO)
        }
        if (uri.rawQuery != null || uri.rawFragment != null) {
            return UrlCheck.Invalid(UrlError.HAS_QUERY_OR_FRAGMENT)
        }

        // 主机名统一小写并去掉末尾根域点：`LOCALHOST.` 与 `localhost` 是同一目标。
        // IPv6 字面量在 java.net.URI 里带方括号，比较前先剥掉。
        val host = if (rawHost.startsWith("[") && rawHost.endsWith("]")) {
            rawHost.substring(1, rawHost.length - 1).lowercase()
        } else {
            rawHost.trimEnd('.').lowercase()
        }
        if (host.isEmpty()) return UrlCheck.Invalid(UrlError.INVALID_HOST)
        val isIpv6 = host.contains(':')
        if (isIpv6) {
            if (!IPV6.matches(host)) return UrlCheck.Invalid(UrlError.INVALID_HOST)
        } else if (!HOSTNAME.matches(host)) {
            return UrlCheck.Invalid(UrlError.INVALID_HOST)
        } else if (!isValidIpv4IfLiteral(host)) {
            return UrlCheck.Invalid(UrlError.INVALID_HOST)
        }

        val port = uri.port
        if (port != -1 && port !in 1..65535) {
            return UrlCheck.Invalid(UrlError.INVALID_PORT)
        }
        if (scheme == "http" && !(allowCleartext && host in DEBUG_CLEARTEXT_HOSTS)) {
            return UrlCheck.Invalid(UrlError.CLEARTEXT_NOT_ALLOWED)
        }

        val rawPath = uri.rawPath ?: ""
        if (hasDotSegment(rawPath) || containsEncodedSeparator(rawPath)) {
            return UrlCheck.Invalid(UrlError.INVALID_PATH)
        }
        var path = rawPath
        if (path.isEmpty()) path = "/"
        if (!path.startsWith("/")) path = "/$path"
        if (!path.endsWith("/")) path = "$path/"

        val hostPart = if (isIpv6) "[$host]" else host
        val portPart = if (port != -1) ":$port" else ""
        return UrlCheck.Ok("$scheme://$hostPart$portPart$path")
    }

    /**
     * 形如 `999.999.999.999` 的主机名能通过 [HOSTNAME]，但每个八位组必须 ≤ 255，
     * 否则会把校验通过、请求时才失败的地址放进登录流程。
     */
    private fun isValidIpv4IfLiteral(host: String): Boolean {
        val parts = host.split('.')
        if (parts.size != 4) return true
        if (parts.any { part -> part.isEmpty() || part.any { !it.isDigit() } }) return true
        return parts.all { part -> part.toIntOrNull()?.let { it in 0..255 } == true }
    }

    private fun hasDotSegment(rawPath: String): Boolean =
        rawPath.split('/').any { it == "." || it == ".." }

    /**
     * `%2e` / `%2f` 解码后会变成新的点段或路径分隔符，URI 与 OkHttp 的规整时机不同，
     * 不拒绝就会出现“校验时是 /a、请求时变成 /../b”的绕过。
     */
    private fun containsEncodedSeparator(rawPath: String): Boolean {
        val lower = rawPath.lowercase()
        return lower.contains("%2e") || lower.contains("%2f") || lower.contains("%5c")
    }
}
