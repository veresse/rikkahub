import React, { useState, useEffect } from 'react';
import { MemoryItem, Assistant } from '../types';
import { api } from '../api';
import { Trash2, Plus, Brain, Calendar } from 'lucide-react';

interface MemoriesViewProps {
  assistants: Assistant[];
}

export const MemoriesView: React.FC<MemoriesViewProps> = ({ assistants }) => {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [selectedAstId, setSelectedAstId] = useState<string>('all');
  const [newContent, setNewContent] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  const loadMemories = async () => {
    try {
      const astParam = selectedAstId === 'all' ? undefined : selectedAstId;
      const data = await api.getMemories(astParam);
      setMemories(data);
    } catch (e) {
      console.error('加载记忆失败:', e);
    }
  };

  useEffect(() => {
    loadMemories();
  }, [selectedAstId]);

  const handleAdd = async () => {
    if (!newContent.trim()) return;
    try {
      await api.createMemory({
        assistant_id: selectedAstId === 'all' ? null : selectedAstId,
        content: newContent.trim(),
      });
      setNewContent('');
      setIsAdding(false);
      loadMemories();
    } catch (e) {
      alert('添加记忆失败: ' + String(e));
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确认删除这条记忆吗？')) return;
    try {
      await api.deleteMemory(id);
      loadMemories();
    } catch (e) {
      alert('删除失败: ' + String(e));
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-50 p-4 sm:p-6 overflow-y-auto">
      {/* 顶栏 */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-b border-slate-200 pb-4">
        <div>
          <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
            <Brain className="h-5 w-5 text-emerald-600" />
            <span>长期记忆库</span>
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            这里存储着陪伴角色对你的全部长期记忆，对话时会自动结合这些记忆回答你
          </p>
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <select
            value={selectedAstId}
            onChange={(e) => setSelectedAstId(e.target.value)}
            className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
          >
            <option value="all">全部角色与全局记忆</option>
            {assistants.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} 的专属记忆
              </option>
            ))}
          </select>

          <button
            onClick={() => setIsAdding(!isAdding)}
            className="flex items-center gap-1 rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>新增记忆</span>
          </button>
        </div>
      </div>

      {/* 新增输入表单 */}
      {isAdding && (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-emerald-900 mb-2">手动添加记忆条目</h3>
          <textarea
            rows={3}
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="例如：喜欢在晚上喝热牛奶；周末喜欢去海边散步..."
            className="w-full rounded-lg border border-emerald-300 p-2.5 text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 bg-white"
          />
          <div className="flex justify-end gap-2 mt-2">
            <button
              onClick={() => setIsAdding(false)}
              className="rounded-lg px-3 py-1 text-xs text-slate-600 hover:bg-slate-200/60"
            >
              取消
            </button>
            <button
              onClick={handleAdd}
              className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700"
            >
              确认保存
            </button>
          </div>
        </div>
      )}

      {/* 记忆列表 */}
      <div className="mt-4 space-y-2.5">
        {memories.length > 0 ? (
          memories.map((mem) => {
            const ast = assistants.find((a) => a.id === mem.assistant_id);
            return (
              <div
                key={mem.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-slate-200/80 bg-white p-3.5 shadow-sm hover:shadow transition-shadow"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold border ${
                        ast
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-slate-100 text-slate-600 border-slate-200'
                      }`}
                    >
                      {ast ? ast.name : '全局共享'}
                    </span>
                    <span className="text-[11px] text-slate-400 flex items-center gap-1">
                      <Calendar className="h-3 w-3" />
                      {new Date(mem.updated_at).toLocaleDateString()}
                    </span>
                  </div>
                  <p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap select-text">
                    {mem.content}
                  </p>
                </div>

                <button
                  onClick={() => handleDelete(mem.id)}
                  className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors"
                  title="删除记忆"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            );
          })
        ) : (
          <div className="py-12 text-center text-xs text-slate-400">
            暂无记忆条目，可通过上方「新增记忆」或导入手机端备份还原
          </div>
        )}
      </div>
    </div>
  );
};
