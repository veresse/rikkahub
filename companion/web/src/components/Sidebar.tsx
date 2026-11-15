import React from 'react';
import { MessageSquare, Bot, Brain, FileArchive, Smartphone } from 'lucide-react';

export type NavTab = 'chat' | 'assistants' | 'memories' | 'backup' | 'devices';

interface SidebarProps {
  currentTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ currentTab, onSelectTab }) => {
  const navItems = [
    { id: 'chat' as NavTab, label: '对话', icon: MessageSquare },
    { id: 'assistants' as NavTab, label: '角色设定', icon: Bot },
    { id: 'memories' as NavTab, label: '长期记忆', icon: Brain },
    { id: 'backup' as NavTab, label: '数据备份', icon: FileArchive },
    { id: 'devices' as NavTab, label: '实体挂件', icon: Smartphone },
  ];

  return (
    <>
      {/* 桌面端左侧导航栏 */}
      <aside className="hidden sm:flex w-56 flex-col border-r border-slate-200 bg-white shrink-0 select-none">
        <div className="flex h-14 items-center gap-2 px-5 border-b border-slate-200">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-600 text-white font-bold text-xs shadow-sm">
            R
          </div>
          <span className="font-bold text-sm tracking-tight text-slate-800">
            RikkaHub 伴侣
          </span>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = currentTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectTab(item.id)}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-xs font-semibold transition-all ${
                  isActive
                    ? 'bg-emerald-50 text-emerald-700 shadow-sm'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`}
              >
                <Icon className={`h-4 w-4 ${isActive ? 'text-emerald-600' : 'text-slate-400'}`} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="p-3 border-t border-slate-100 text-[11px] text-slate-400 text-center">
          v1.0 权威云端同步版
        </div>
      </aside>

      {/* 移动端底部导航栏 */}
      <nav className="flex sm:hidden fixed bottom-0 left-0 right-0 h-14 bg-white/95 border-t border-slate-200 backdrop-blur-md justify-around items-center z-30 safe-bottom">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectTab(item.id)}
              className={`flex flex-col items-center justify-center w-full h-full py-1 ${
                isActive ? 'text-emerald-600 font-semibold' : 'text-slate-400'
              }`}
            >
              <Icon className="h-4 w-4" />
              <span className="text-[10px] mt-0.5">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </>
  );
};
