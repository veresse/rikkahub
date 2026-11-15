import React, { useState, useEffect } from 'react';
import { Sidebar, NavTab } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { AssistantsView } from './components/AssistantsView';
import { MemoriesView } from './components/MemoriesView';
import { BackupView } from './components/BackupView';
import { DevicesView } from './components/DevicesView';
import { Assistant } from './types';
import { api } from './api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<NavTab>('chat');
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [activeAssistant, setActiveAssistant] = useState<Assistant | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchAssistants = async () => {
    try {
      const list = await api.getAssistants();
      setAssistants(list);
      if (list.length > 0 && !activeAssistant) {
        setActiveAssistant(list[0]);
      }
    } catch (e) {
      console.error('加载角色失败:', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAssistants();
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-100">
      <Sidebar currentTab={currentTab} onSelectTab={setCurrentTab} />

      <main className="flex-1 flex flex-col h-full overflow-hidden pb-14 sm:pb-0 relative">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-slate-400 text-xs">
            正在连接云端伴侣服务...
          </div>
        ) : (
          <>
            {currentTab === 'chat' && (
              <ChatView
                currentAssistant={activeAssistant}
                onOpenSidebar={() => {}}
              />
            )}
            {currentTab === 'assistants' && (
              <AssistantsView
                assistants={assistants}
                onRefresh={fetchAssistants}
                onSelectAssistant={(ast) => setActiveAssistant(ast)}
              />
            )}
            {currentTab === 'memories' && (
              <MemoriesView assistants={assistants} />
            )}
            {currentTab === 'backup' && (
              <BackupView onImportComplete={fetchAssistants} />
            )}
            {currentTab === 'devices' && (
              <DevicesView assistants={assistants} />
            )}
          </>
        )}
      </main>
    </div>
  );
};

export default App;
