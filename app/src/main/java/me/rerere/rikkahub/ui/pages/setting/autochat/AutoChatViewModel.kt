package me.rerere.rikkahub.ui.pages.setting.autochat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import me.rerere.rikkahub.data.datastore.SettingsStore
import me.rerere.rikkahub.data.model.AutoChatRecord
import me.rerere.rikkahub.data.model.AutoChatRule
import me.rerere.rikkahub.data.model.Conversation
import me.rerere.rikkahub.data.repository.AutoChatRepository
import me.rerere.rikkahub.data.repository.ConversationRepository
import me.rerere.rikkahub.service.autochat.AutoChatExecutionResult
import me.rerere.rikkahub.service.autochat.AutoChatScheduler
import kotlin.uuid.Uuid

sealed class TestState {
    data object Idle : TestState()
    data object Testing : TestState()
    data class Success(val text: String) : TestState()
    data class Failed(val error: String) : TestState()
}

class AutoChatViewModel(
    private val repository: AutoChatRepository,
    private val scheduler: AutoChatScheduler,
    private val settingsStore: SettingsStore,
    private val conversationRepo: ConversationRepository,
) : ViewModel() {

    val rules: StateFlow<List<AutoChatRule>> = repository.getAllRules()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val recentRecords: StateFlow<List<AutoChatRecord>> = repository.getRecentRecords(100)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    private val _testState = MutableStateFlow<TestState>(TestState.Idle)
    val testState: StateFlow<TestState> = _testState.asStateFlow()

    fun resetTestState() {
        _testState.value = TestState.Idle
    }

    fun saveRule(rule: AutoChatRule, onSaved: () -> Unit = {}) {
        viewModelScope.launch {
            val exists = repository.getRuleById(rule.id)
            if (exists == null) {
                repository.insertRule(rule)
            } else {
                repository.updateRule(rule.copy(version = rule.version + 1))
            }
            scheduler.reconcile()
            onSaved()
        }
    }

    fun toggleRuleEnabled(rule: AutoChatRule) {
        viewModelScope.launch {
            val updated = rule.copy(
                enabled = !rule.enabled,
                version = rule.version + 1,
            )
            repository.updateRule(updated)
            if (updated.enabled) {
                scheduler.reconcile()
            } else {
                scheduler.cancelRule(rule.id)
            }
        }
    }

    fun deleteRule(ruleId: Uuid, onDeleted: () -> Unit = {}) {
        viewModelScope.launch {
            scheduler.cancelRule(ruleId)
            repository.deleteRule(ruleId)
            scheduler.reconcile()
            onDeleted()
        }
    }

    fun clearHistory() {
        viewModelScope.launch {
            repository.deleteAllRecords()
        }
    }

    fun testRule(rule: AutoChatRule) {
        viewModelScope.launch {
            _testState.value = TestState.Testing
            val result = scheduler.triggerManualTest(rule)
            _testState.value = when (result) {
                is AutoChatExecutionResult.Success -> TestState.Success(result.text)
                is AutoChatExecutionResult.Failed -> TestState.Failed(result.error.message ?: "Unknown error")
                is AutoChatExecutionResult.Skipped -> TestState.Failed("Skipped: ${result.reason}")
            }
        }
    }

    suspend fun getConversationsForAssistant(assistantId: Uuid): List<Conversation> =
        conversationRepo.getConversationsOfAssistant(assistantId).first()
}
