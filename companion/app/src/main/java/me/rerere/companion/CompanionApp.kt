package me.rerere.companion

import android.app.Application
import me.rerere.companion.core.AppContainer

class CompanionApp : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
