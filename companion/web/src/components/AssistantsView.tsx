import React, { useState } from 'react';
import { Assistant } from '../types';
import { api } from '../api';
import { Bot, Save, Sparkles } from 'lucide-react';

interface AssistantsViewProps {
  assistants: Assistant[];
  onRefresh: () => void;
  onSelectAssistant: (ast: Assistant) => void;
}

export const AssistantsView: React.FC<AssistantsViewProps> = ({
  assistants,
  onRefresh,
  onSelectAssistant,
}) => {
  const [editingAst, setEditingAst] = useState<Assistant | null>(assistants[0] || null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const handleSave = async () => {
    if (!editingAst) return;
    setIsSaving(true);
    try {
      await api.updateAssistant(editingAst.id, editingAst);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
      onRefresh();
    } catch (e) {
      alert('保存失败: ' + String(e));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col sm:flex-row h-full overflow-hidden bg-slate-50">
      {/* 角色列表侧边栏 */}
      <div className="w-full sm:w-64 border-r border-slate-200 bg-white p-4 overflow-y-auto shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
          全部助手角色 ({assistants.length})
        </h2>
        <div className="space-y-1.5">
          {assistants.map((ast) => (
            <button
              key={ast.id}
              onClick={() => {
                setEditingAst(ast);
                onSelectAssistant(ast);
              }}
              className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm transition-all ${
                editingAst?.id === ast.id
                  ? 'bg-emerald-50 text-emerald-900 font-semibold border border-emerald-200/80 shadow-sm'
                  : 'text-slate-700 hover:bg-slate-50'
              }`}
            >
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white font-bold text-xs">
                {ast.name.slice(0, 1)}
              </div>
              <span className="truncate flex-1">{ast.name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* 角色人设详情编辑区 */}
      {editingAst ? (
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-5">
          <div className="flex items-center justify-between border-b border-slate-200 pb-4">
            <div>
              <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                {editingAst.name}
                <span className="text-xs font-normal text-slate-400">({editingAst.id.slice(0, 8)})</span>
              </h1>
              <p className="text-xs text-slate-500 mt-0.5">配置角色的核心人设、提示词和思考逻辑</p>
            </div>
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow hover:bg-emerald-700 active:scale-95 disabled:opacity-50 transition-all"
            >
              <Save className="h-4 w-4" />
              <span>{isSaving ? '保存中...' : saveSuccess ? '已保存！' : '保存配置'}</span>
            </button>
          </div>

          <div className="grid grid-cols-1 gap-4 max-w-2xl">
            {/* 角色名称 */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">角色名称</label>
              <input
                type="text"
                value={editingAst.name}
                onChange={(e) => setEditingAst({ ...editingAst, name: e.target.value })}
                className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 focus:outline-none"
              />
            </div>

            {/* 设定提示词 (System Prompt) */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1">
                设定提示词 (System Prompt)
              </label>
              <textarea
                rows={6}
                value={editingAst.system_prompt}
                onChange={(e) => setEditingAst({ ...editingAst, system_prompt: e.target.value })}
                className="w-full rounded-xl border border-slate-300 p-3 text-sm text-slate-800 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 focus:outline-none font-mono text-xs leading-relaxed"
                placeholder="例如：你是夏以昼，温柔体贴..."
              />
            </div>

            {/* 开关选项 */}
            <div className="space-y-3 pt-2">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editingAst.enable_memory}
                  onChange={(e) => setEditingAst({ ...editingAst, enable_memory: e.target.checked })}
                  className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                />
                <div>
                  <div className="text-xs font-semibold text-slate-800">启用长期记忆注入</div>
                  <div className="text-[11px] text-slate-500">将记忆库中的用户喜好和背景注入到每次对话中</div>
                </div>
              </label>

              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={editingAst.enable_web_search}
                  onChange={(e) => setEditingAst({ ...editingAst, enable_web_search: e.target.checked })}
                  className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                />
                <div>
                  <div className="text-xs font-semibold text-slate-800">自动联网搜索</div>
                  <div className="text-[11px] text-slate-500">当问题包含时效信息时，自动检索互联网</div>
                </div>
              </label>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-slate-400">
          请选择一个角色进行查看与配置
        </div>
      )}
    </div>
  );
};
