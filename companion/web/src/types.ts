export interface Assistant {
  id: string;
  name: string;
  system_prompt: string;
  temperature: number | null;
  top_p: number | null;
  context_message_limit: number;
  enable_memory: boolean;
  use_global_memory: boolean;
  enable_web_search: boolean;
  avatar: Record<string, any>;
}

export interface ConversationSummary {
  id: string;
  assistant_id: string;
  title: string;
  is_pinned: boolean;
  created_at: number;
  updated_at: number;
}

export interface UIMessagePart {
  text?: string;
  reasoning?: string;
  tool?: Record<string, any>;
}

export interface UIMessage {
  id: string;
  role: 'USER' | 'ASSISTANT' | 'SYSTEM' | 'TOOL';
  parts: UIMessagePart[];
  createdAt: string;
}

export interface MessageNode {
  id: string;
  node_index: number;
  messages: UIMessage[];
  select_index: number;
  total_branches: number;
}

export interface ConversationDetail {
  id: string;
  assistant_id: string;
  title: string;
  is_pinned: boolean;
  custom_system_prompt: string;
  created_at: number;
  updated_at: number;
  nodes: MessageNode[];
}

export interface MemoryItem {
  id: string;
  assistant_id: string | null;
  path: string;
  content: string;
  updated_at: number;
}

export interface DeviceItem {
  id: string;
  name: string;
  bound_assistant_id: string | null;
  bound_conversation_id: string | null;
  is_active: boolean;
  created_at: number;
  last_seen_at: number | null;
}

export interface ImportPreviewReport {
  preview_id: string;
  summary: {
    assistant_count: number;
    conversation_count: number;
    message_node_count: number;
    total_message_versions: number;
    memory_count: number;
    unsupported_features: string[];
  };
  assistants_sample: string[];
  conversations_sample: string[];
}
