import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import { Assistant, ConversationDetail, MessageNode } from '../types';
import { MessageBubble } from './MessageBubble';
import { Send, Square, Plus, MessageSquare, ChevronDown, Sparkles, Globe } from 'lucide-react';

interface ChatViewProps {
  currentAssistant: Assistant | null;
  onOpenSidebar: () => void;
}

export const ChatView: React.FC<ChatViewProps> = ({ currentAssistant }) => {
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [convDetail, setConvDetail] = useState<ConversationDetail | null>(null);
  const [inputVal, setInputVal] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [searchStatus, setSearchStatus] = useState<string | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 1. 加载当前角色的会话列表
  const loadConversations = async (astId: string) => {
    try {
      const list = await api.getConversations(astId);
      setConversations(list);
      if (list.length > 0) {
        setActiveConvId(list[0].id);
      } else {
        // 无会话时自动创建一个
        const newConv = await api.createConversation(astId, `与${currentAssistant?.name || '助手'}的对话`);
        setActiveConvId(newConv.id);
        setConversations([{ id: newConv.id, title: `与${currentAssistant?.name || '助手'}的对话` }]);
      }
    } catch (e) {
      console.error('加载会话失败:', e);
    }
  };

  useEffect(() => {
    if (currentAssistant) {
      loadConversations(currentAssistant.id);
    }
  }, [currentAssistant]);

  // 2. 加载选中的会话详情
  const loadDetail = async (convId: string) => {
    try {
      const detail = await api.getConversationDetail(convId);
      setConvDetail(detail);
    } catch (e) {
      console.error('加载对话详情失败:', e);
    }
  };

  useEffect(() => {
    if (activeConvId) {
      loadDetail(activeConvId);
    }
  }, [activeConvId]);

  // 滚动到底部
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [convDetail?.nodes, isGenerating]);

  // 3. 发送消息与流式响应
  const handleSend = async (regenerateNodeId?: string) => {
    if (!inputVal.trim() && !regenerateNodeId) return;
    if (!activeConvId || isGenerating) return;

    const userText = inputVal.trim();
    setInputVal('');
    setIsGenerating(true);
    setSearchStatus(null);

    // 乐观添加本地临时用户节点 (如果不是重新生成)
    if (!regenerateNodeId && convDetail) {
      const tempUserNode: MessageNode = {
        id: 'temp-user-' + Date.now(),
        node_index: convDetail.nodes.length,
        messages: [{
          id: 'temp-msg-u',
          role: 'USER',
          parts: [{ text: userText }],
          createdAt: new Date().toISOString(),
        }],
        select_index: 0,
        total_branches: 1,
      };

      const tempAssistantNode: MessageNode = {
        id: 'temp-ast-' + Date.now(),
        node_index: convDetail.nodes.length + 1,
        messages: [{
          id: 'temp-msg-a',
          role: 'ASSISTANT',
          parts: [{ text: '', reasoning: '' }],
          createdAt: new Date().toISOString(),
        }],
        select_index: 0,
        total_branches: 1,
      };

      setConvDetail({
        ...convDetail,
        nodes: [...convDetail.nodes, tempUserNode, tempAssistantNode],
      });
    }

    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch('/api/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: activeConvId,
          message: userText,
          regenerate_node_id: regenerateNodeId,
        }),
        signal: abortControllerRef.current.signal,
      });

      if (!response.body) throw new Error('流式读取不可用');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamBuffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        streamBuffer += decoder.decode(value, { stream: true });
        const lines = streamBuffer.split('\n\n');
        streamBuffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'status') {
              if (data.status === 'searching') setSearchStatus('正在联网检索实时信息...');
              if (data.status === 'search_done') setSearchStatus(null);
            } else if (data.type === 'content') {
              setConvDetail((prev) => {
                if (!prev) return prev;
                const newNodes = [...prev.nodes];
                const lastNode = newNodes[newNodes.length - 1];
                if (lastNode && lastNode.messages.length > 0) {
                  const targetMsg = lastNode.messages[lastNode.select_index];
                  const textPart = targetMsg.parts.find((p) => 'text' in p) || { text: '' };
                  textPart.text = (textPart.text || '') + data.delta;
                }
                return { ...prev, nodes: newNodes };
              });
            } else if (data.type === 'reasoning') {
              setConvDetail((prev) => {
                if (!prev) return prev;
                const newNodes = [...prev.nodes];
                const lastNode = newNodes[newNodes.length - 1];
                if (lastNode && lastNode.messages.length > 0) {
                  const targetMsg = lastNode.messages[lastNode.select_index];
                  const rPart = targetMsg.parts.find((p) => 'reasoning' in p) || { reasoning: '' };
                  rPart.reasoning = (rPart.reasoning || '') + data.delta;
                }
                return { ...prev, nodes: newNodes };
              });
            } else if (data.type === 'done') {
              setIsGenerating(false);
              setSearchStatus(null);
              // 重新拉取一次落库权威数据
              loadDetail(activeConvId);
            }
          } catch (e) {
            console.error('SSE JSON 解析错误:', e);
          }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        console.error('聊天生成异常:', e);
      }
    } finally {
      setIsGenerating(false);
      setSearchStatus(null);
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      setIsGenerating(false);
      setSearchStatus(null);
    }
  };

  const handleSelectBranch = async (nodeId: string, index: number) => {
    if (!activeConvId) return;
    try {
      await api.selectNodeBranch(activeConvId, nodeId, index);
      // 本地同步更新选中状态
      setConvDetail((prev) => {
        if (!prev) return prev;
        const newNodes = prev.nodes.map((n) => (n.id === nodeId ? { ...n, select_index: index } : n));
        return { ...prev, nodes: newNodes };
      });
    } catch (e) {
      console.error('切换分支失败:', e);
    }
  };

  const handleNewConversation = async () => {
    if (!currentAssistant) return;
    const newConv = await api.createConversation(currentAssistant.id, `对话 ${new Date().toLocaleDateString()}`);
    setConversations([newConv, ...conversations]);
    setActiveConvId(newConv.id);
  };

  return (
    <div className="flex h-full flex-col bg-slate-50 relative">
      {/* 顶栏 */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200/80 bg-white/80 px-4 backdrop-blur-md sticky top-0 z-10">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-600 text-white font-bold text-sm shadow-sm">
            {currentAssistant?.name.slice(0, 1) || 'A'}
          </div>
          <div>
            <h1 className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
              {currentAssistant?.name || '选择角色'}
              {currentAssistant?.enable_memory && (
                <span className="rounded bg-emerald-50 px-1 py-0.2 text-[10px] font-normal text-emerald-600 border border-emerald-200/60">
                  记忆中
                </span>
              )}
            </h1>
            <p className="text-[11px] text-slate-400 truncate max-w-[200px]">
              {convDetail?.title || '未命名对话'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleNewConversation}
            className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 active:scale-95 transition-all"
          >
            <Plus className="h-3.5 w-3.5 text-emerald-600" />
            <span className="hidden sm:inline">新对话</span>
          </button>
        </div>
      </header>

      {/* 消息展示区域 */}
      <div className="flex-1 overflow-y-auto px-1 py-4 sm:px-4">
        {convDetail?.nodes && convDetail.nodes.length > 0 ? (
          convDetail.nodes.map((node) => (
            <MessageBubble
              key={node.id}
              node={node}
              assistantName={currentAssistant?.name || '助手'}
              onSelectBranch={handleSelectBranch}
              onRegenerate={() => handleSend(node.id)}
            />
          ))
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center p-6 text-slate-400">
            <Sparkles className="h-10 w-10 text-emerald-500/40 mb-2" />
            <p className="text-sm font-medium text-slate-600">与 {currentAssistant?.name} 开始对话吧</p>
            <p className="text-xs text-slate-400 mt-1">你的所有对话和记忆都将在云端实时同步与保存</p>
          </div>
        )}

        {searchStatus && (
          <div className="flex items-center gap-2 px-6 py-2 text-xs text-emerald-700 bg-emerald-50/70 border border-emerald-200/50 rounded-lg mx-4 my-2 animate-pulse">
            <Globe className="h-3.5 w-3.5 text-emerald-600" />
            <span>{searchStatus}</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* 底部输入框 */}
      <div className="p-3 bg-white/90 border-t border-slate-200/80 backdrop-blur-md safe-bottom">
        <div className="relative flex items-center rounded-2xl border border-slate-300/80 bg-white shadow-sm focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-500/20 transition-all">
          <textarea
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={`发消息给 ${currentAssistant?.name || '助手'}... (Enter 发送)`}
            rows={1}
            disabled={isGenerating}
            className="flex-1 resize-none bg-transparent py-3 pl-4 pr-12 text-sm text-slate-800 placeholder-slate-400 focus:outline-none max-h-32 min-h-[46px]"
          />

          <div className="absolute right-2 flex items-center">
            {isGenerating ? (
              <button
                onClick={handleStop}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-rose-500 text-white shadow-sm hover:bg-rose-600 transition-colors"
                title="停止生成"
              >
                <Square className="h-4 w-4" />
              </button>
            ) : (
              <button
                onClick={() => handleSend()}
                disabled={!inputVal.trim()}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 disabled:opacity-40 disabled:hover:bg-emerald-600 transition-all active:scale-95"
                title="发送"
              >
                <Send className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
