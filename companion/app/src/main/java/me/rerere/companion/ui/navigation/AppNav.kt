package me.rerere.companion.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import me.rerere.companion.core.AppContainer
import me.rerere.companion.feature.home.HomePlaceholderScreen
import me.rerere.companion.feature.login.LoginScreen
import me.rerere.companion.feature.login.LoginViewModel

@Composable
fun AppNav(container: AppContainer) {
    val session by container.sessionManager.state.collectAsStateWithLifecycle()

    LaunchedEffect(container) {
        container.sessionManager.restore()
    }

    val current = session
    if (current == null) {
        LoginRoute(container)
    } else {
        HomePlaceholderScreen(
            serverUrl = current.serverUrl,
            onLogout = container::logout,
        )
    }
}

@Composable
private fun LoginRoute(container: AppContainer) {
    val viewModel: LoginViewModel = viewModel(
        factory = viewModelFactory {
            initializer {
                LoginViewModel(
                    repository = container.authRepository,
                    allowCleartext = container.allowCleartext,
                )
            }
        },
    )
    val state by viewModel.state.collectAsStateWithLifecycle()

    LoginScreen(
        state = state,
        onServerUrlChange = viewModel::onServerUrlChange,
        onUsernameChange = viewModel::onUsernameChange,
        onPasswordChange = viewModel::onPasswordChange,
        onSubmit = viewModel::submit,
    )
}
