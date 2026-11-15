import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ThinkingBox } from './ThinkingBox';
import { BranchSelector } from './BranchSelector';
import { MessageNode, UIMessage } from '../types';
import { Globe, User, Bot } from 'lucide-react';

interface MessageBubbleProps {
  node: MessageNode;
  assistantName: string;
  onSelectBranch: (nodeId: string, index: number) => void;
  onRegenerate?: (nodeId: string) => void;
  isStreaming?: boolean;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  node,
  assistantName,
  onSelectBranch,
  onRegenerate,
  isStreaming = false,
}) => {
  const currentMsg: UIMessage | undefined = node.messages[node.select_index] || node.messages[0];
  if (!currentMsg) return null;

  const isUser = currentMsg.role === 'USER';

  // 提取文本和思考过程
  let textContent = '';
  let reasoningContent = '';

  for (const part of currentMsg.parts) {
    if (part.text) textContent += part.text;
    if (part.reasoning) reasoningContent += part.reasoning;
  }

  return (
    <div className={`flex w-full gap-2.5 px-3 py-2 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-full bg-emerald-600 text-white font-semibold text-xs shadow-sm">
          {assistantName ? assistantName.slice(0, 1) : <Bot className="h-4 w-4" />}
        </div>
      )}

      <div className={`flex max-w-[85%] sm:max-w-[75%] flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {!isUser && (
          <span className="mb-1 text-[11px] font-medium text-slate-500">
            {assistantName}
          </span>
        )}

        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm select-text ${
            isUser
              ? 'bg-emerald-600 text-white rounded-br-none'
              : 'bg-white border border-slate-200/80 text-slate-800 rounded-bl-none'
          }`}
        >
          {/* 助手消息中的深度思考折叠框 */}
          {!isUser && reasoningContent && (
            <ThinkingBox content={reasoningContent} />
          )}

          {/* 正文渲染 */}
          {textContent ? (
            <div className={`markdown-body ${isUser ? 'text-white' : 'text-slate-800'}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {textContent}
              </ReactMarkdown>
            </div>
          ) : isStreaming ? (
            <div className="flex items-center gap-1.5 py-1 text-slate-400">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-bounce" />
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-bounce [animation-delay:0.2s]" />
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-bounce [animation-delay:0.4s]" />
            </div>
          ) : null}
        </div>

        {/* 助手消息下方的分支切换条 */}
        {!isUser && !isStreaming && (
          <BranchSelector
            currentIndex={node.select_index}
            totalBranches={node.messages.length}
            onSelect={(idx) => onSelectBranch(node.id, idx)}
            onRegenerate={onRegenerate ? () => onRegenerate(node.id) : undefined}
          />
        )}
      </div>

      {isUser && (
        <div className="flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-full bg-slate-200 text-slate-600 font-semibold text-xs shadow-sm">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  );
};
