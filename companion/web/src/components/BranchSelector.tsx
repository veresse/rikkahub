import React from 'react';
import { ChevronLeft, ChevronRight, RotateCw } from 'lucide-react';

interface BranchSelectorProps {
  currentIndex: number;
  totalBranches: number;
  onSelect: (newIndex: number) => void;
  onRegenerate?: () => void;
  disabled?: boolean;
}

export const BranchSelector: React.FC<BranchSelectorProps> = ({
  currentIndex,
  totalBranches,
  onSelect,
  onRegenerate,
  disabled = false,
}) => {
  return (
    <div className="flex items-center gap-1.5 text-xs text-slate-500 mt-1 select-none">
      {totalBranches > 1 && (
        <div className="flex items-center rounded-md bg-slate-100 px-1 py-0.5 border border-slate-200">
          <button
            disabled={disabled || currentIndex <= 0}
            onClick={() => onSelect(currentIndex - 1)}
            className="p-0.5 hover:text-slate-800 disabled:opacity-30 disabled:hover:text-slate-500 transition-colors"
            title="查看上一候选版本"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <span className="px-1.5 font-mono font-medium text-[11px]">
            {currentIndex + 1}/{totalBranches}
          </span>
          <button
            disabled={disabled || currentIndex >= totalBranches - 1}
            onClick={() => onSelect(currentIndex + 1)}
            className="p-0.5 hover:text-slate-800 disabled:opacity-30 disabled:hover:text-slate-500 transition-colors"
            title="查看下一候选版本"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {onRegenerate && (
        <button
          disabled={disabled}
          onClick={onRegenerate}
          className="flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-slate-100 hover:text-slate-800 transition-colors"
          title="重新生成并增加新分支"
        >
          <RotateCw className="h-3 w-3" />
          <span className="text-[11px]">重新回答</span>
        </button>
      )}
    </div>
  );
};
