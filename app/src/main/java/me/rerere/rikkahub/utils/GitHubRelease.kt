package me.rerere.rikkahub.utils

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.Locale

private val releaseJson = Json { ignoreUnknownKeys = true }

internal fun parseGitHubRelease(payload: String): UpdateInfo {
    val release = releaseJson.decodeFromString<GitHubRelease>(payload)
    val downloads = release.assets
        .filter { it.name.endsWith(".apk", ignoreCase = true) }
        .map { asset ->
            UpdateDownload(
                name = asset.name,
                url = asset.downloadUrl,
                size = String.format(Locale.ROOT, "%.1f MB", asset.size / 1048576.0),
            )
        }
    require(downloads.isNotEmpty()) { "GitHub release has no APK downloads" }
    // Existing custom release tags describe builds of the same base version.
    val version = release.tagName.removePrefix("v")
        .replace(Regex("-custom-(\\d+)$"), "+custom.$1")
    return UpdateInfo(
        version = version,
        publishedAt = release.publishedAt,
        changelog = release.body.orEmpty(),
        downloads = downloads,
    )
}

@Serializable
private data class GitHubRelease(
    @SerialName("tag_name") val tagName: String,
    @SerialName("published_at") val publishedAt: String,
    val body: String? = null,
    val assets: List<GitHubReleaseAsset>,
)

@Serializable
private data class GitHubReleaseAsset(
    val name: String,
    @SerialName("browser_download_url") val downloadUrl: String,
    val size: Long,
)
