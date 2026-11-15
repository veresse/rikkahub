import {
  Assistant,
  ConversationSummary,
  ConversationDetail,
  MemoryItem,
  DeviceItem,
  ImportPreviewReport
} from './types';

const API_BASE = '/api';

export const api = {
  // 角色
  async getAssistants(): Promise<Assistant[]> {
    const res = await fetch(`${API_BASE}/assistants`);
    return res.json();
  },
  async getAssistant(id: string): Promise<Assistant> {
    const res = await fetch(`${API_BASE}/assistants/${id}`);
    return res.json();
  },
  async updateAssistant(id: string, data: Partial<Assistant>): Promise<void> {
    await fetch(`${API_BASE}/assistants/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  // 会话与消息树
  async getConversations(assistantId?: string): Promise<ConversationSummary[]> {
    const url = assistantId ? `${API_BASE}/conversations?assistant_id=${assistantId}` : `${API_BASE}/conversations`;
    const res = await fetch(url);
    return res.json();
  },
  async getConversationDetail(id: string): Promise<ConversationDetail> {
    const res = await fetch(`${API_BASE}/conversations/${id}`);
    return res.json();
  },
  async createConversation(assistantId: string, title = '新对话'): Promise<{ id: string }> {
    const res = await fetch(`${API_BASE}/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assistant_id: assistantId, title }),
    });
    return res.json();
  },
  async deleteConversation(id: string): Promise<void> {
    await fetch(`${API_BASE}/conversations/${id}`, { method: 'DELETE' });
  },
  // 切换分支
  async selectNodeBranch(convId: string, nodeId: string, index: number): Promise<void> {
    await fetch(`${API_BASE}/conversations/${convId}/nodes/${nodeId}/select?index=${index}`, {
      method: 'POST',
    });
  },

  // 记忆
  async getMemories(assistantId?: string): Promise<MemoryItem[]> {
    const url = assistantId ? `${API_BASE}/memories?assistant_id=${assistantId}` : `${API_BASE}/memories`;
    const res = await fetch(url);
    return res.json();
  },
  async createMemory(data: { assistant_id?: string | null; content: string }): Promise<void> {
    await fetch(`${API_BASE}/memories`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },
  async deleteMemory(id: string): Promise<void> {
    await fetch(`${API_BASE}/memories/${id}`, { method: 'DELETE' });
  },

  // 挂件设备
  async getDevices(): Promise<DeviceItem[]> {
    const res = await fetch(`${API_BASE}/devices`);
    return res.json();
  },
  async createPairCode(assistantId?: string, conversationId?: string): Promise<{ code: string; expires_in: number }> {
    const res = await fetch(`${API_BASE}/devices/pair-code`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assistant_id: assistantId, conversation_id: conversationId }),
    });
    return res.json();
  },

  // 备份与导入
  async uploadBackupPreview(file: File): Promise<ImportPreviewReport> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/backup/preview`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '备份文件解析失败');
    }
    return res.json();
  },
  async confirmImport(previewId: string): Promise<any> {
    const res = await fetch(`${API_BASE}/backup/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preview_id: previewId }),
    });
    return res.json();
  },
  getExportBackupUrl(): string {
    return `${API_BASE}/backup/export`;
  },
};
