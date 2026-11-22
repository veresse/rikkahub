package me.rerere.rikkahub.utils

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

internal fun pollUpdateInfo(
    intervalMillis: Long = 60 * 60 * 1000L,
    retryMillis: Long = 5 * 60 * 1000L,
    fetch: suspend () -> UpdateInfo,
): Flow<UiState<UpdateInfo>> = flow {
    emit(UiState.Loading)
    while (true) {
        val result = try {
            UiState.Success(fetch())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            UiState.Error(e)
        }
        emit(result)
        delay(if (result is UiState.Error) retryMillis else intervalMillis)
    }
}
