package me.rerere.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import me.rerere.companion.ui.navigation.AppNav
import me.rerere.companion.ui.theme.CompanionTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as CompanionApp).container
        setContent {
            CompanionTheme {
                AppNav(container)
            }
        }
    }
}
