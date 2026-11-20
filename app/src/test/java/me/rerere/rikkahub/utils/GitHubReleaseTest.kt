package me.rerere.rikkahub.utils

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class GitHubReleaseTest {
    @Test
    fun parsesReleaseAndOnlyIncludesApks() {
        val info = parseGitHubRelease(
            """{
                "tag_name":"v3.2.2",
                "published_at":"2026-11-20T12:00:00Z",
                "body":"Release notes",
                "assets":[
                    {"name":"app.apk","browser_download_url":"https://github.com/app.apk","size":1048576},
                    {"name":"checksums.txt","browser_download_url":"https://github.com/checksums.txt","size":100}
                ],
                "unknown_field":true
            }""".trimIndent()
        )
        assertEquals("3.2.2", info.version)
        assertEquals("2026-11-20T12:00:00Z", info.publishedAt)
        assertEquals("Release notes", info.changelog)
        assertEquals(1, info.downloads.size)
        assertEquals("app.apk", info.downloads.single().name)
        assertEquals("https://github.com/app.apk", info.downloads.single().url)
        assertEquals("1.0 MB", info.downloads.single().size)
    }

    @Test
    fun customBuildIsNotTreatedAsPrereleaseAndNotesCanBeNull() {
        val info = parseGitHubRelease(
            """{"tag_name":"v3.2.1-custom-202611191421","published_at":"2026-11-19T14:21:40Z",
                "body":null,"assets":[{"name":"app.apk","browser_download_url":"https://github.com/app.apk","size":0}]}"""
        )
        assertEquals(0, Version.compare(info.version, "3.2.1"))
        assertEquals("", info.changelog)
    }

    @Test
    fun rejectsReleaseWithoutApks() {
        assertThrows(IllegalArgumentException::class.java) {
            parseGitHubRelease("""{"tag_name":"v3.2.2","published_at":"2026-11-20T12:00:00Z","assets":[]}""")
        }
    }
}
