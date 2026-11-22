package me.rerere.rikkahub.utils

internal fun selectUpdateDownload(downloads: List<UpdateDownload>, abis: List<String>): UpdateDownload? =
    abis.firstNotNullOfOrNull { abi -> downloads.firstOrNull { it.name.endsWith("-$abi-release.apk") } }
        ?: downloads.firstOrNull { it.name.endsWith("-universal-release.apk") }

internal fun shouldCheckForUpdate(isPlay: Boolean, settingsLoading: Boolean, disabledUntil: Long, now: Long): Boolean =
    !isPlay && !settingsLoading && disabledUntil <= now
