import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Brain } from 'lucide-react';

interface ThinkingBoxProps {
  content: string;
}

export const ThinkingBox: React.FC<ThinkingBoxProps> = ({ content }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!content) return null;

  return (
    <div className="mb-2 rounded-lg border border-amber-200/60 bg-amber-50/50 text-xs text-amber-900 overflow-hidden shadow-sm">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between px-3 py-1.5 font-medium hover:bg-amber-100/50 transition-colors"
      >
        <div className="flex items-center gap-1.5">
          <Brain className="h-3.5 w-3.5 text-amber-600 animate-pulse" />
          <span>深度思考过程</span>
        </div>
        {isOpen ? (
          <ChevronDown className="h-3.5 w-3.5 text-amber-600" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-amber-600" />
        )}
      </button>

      {isOpen && (
        <div className="border-t border-amber-200/50 bg-amber-50/80 px-3 py-2 leading-relaxed text-amber-800 whitespace-pre-wrap select-text">
          {content}
        </div>
      )}
    </div>
  );
};
