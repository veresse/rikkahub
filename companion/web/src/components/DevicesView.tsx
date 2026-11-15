import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { DeviceItem, Assistant } from '../types';
import { Smartphone, KeyRound, Wifi, RefreshCw } from 'lucide-react';

interface DevicesViewProps {
  assistants: Assistant[];
}

export const DevicesView: React.FC<DevicesViewProps> = ({ assistants }) => {
  const [devices, setDevices] = useState<DeviceItem[]>([]);
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [expiresIn, setExpiresIn] = useState<number>(0);
  const [selectedAstId, setSelectedAstId] = useState<string>('');

  const loadDevices = async () => {
    try {
      const list = await api.getDevices();
      setDevices(list);
    } catch (e) {
      console.error('加载设备失败:', e);
    }
  };

  useEffect(() => {
    loadDevices();
    if (assistants.length > 0) {
      setSelectedAstId(assistants[0].id);
    }
  }, [assistants]);

  // 倒计时
  useEffect(() => {
    if (expiresIn <= 0) return;
    const timer = setInterval(() => {
      setExpiresIn((prev) => {
        if (prev <= 1) {
          setPairCode(null);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [expiresIn]);

  const handleGenerateCode = async () => {
    try {
      const res = await api.createPairCode(selectedAstId || undefined);
      setPairCode(res.code);
      setExpiresIn(res.expires_in);
    } catch (e) {
      alert('生成配对码失败: ' + String(e));
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-50 p-4 sm:p-6 overflow-y-auto max-w-4xl mx-auto space-y-6">
      <div className="border-b border-slate-200 pb-4">
        <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
          <Smartphone className="h-5 w-5 text-emerald-600" />
          <span>实体挂件设备管理</span>
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          为随身携带的 ESP32 语音挂件提供绑定、连接状态与安全配对
        </p>
      </div>

      {/* 配对码卡片 */}
      <div className="rounded-2xl border border-emerald-200 bg-white p-5 shadow-sm space-y-4">
        <div>
          <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
            <KeyRound className="h-4 w-4 text-emerald-600" />
            <span>添加新挂件设备（一键配对）</span>
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            设备开机后通过串口配网工具或热点输入此配对码，即可自动完成安全认证绑定
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center gap-3">
          <select
            value={selectedAstId}
            onChange={(e) => setSelectedAstId(e.target.value)}
            className="w-full sm:w-auto rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700 focus:outline-none"
          >
            {assistants.map((a) => (
              <option key={a.id} value={a.id}>
                默认绑定角色: {a.name}
              </option>
            ))}
          </select>

          <button
            onClick={handleGenerateCode}
            className="w-full sm:w-auto rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow hover:bg-emerald-700 active:scale-95 transition-all"
          >
            生成一次性配对码
          </button>
        </div>

        {pairCode && (
          <div className="rounded-xl bg-emerald-50 border border-emerald-200/80 p-4 text-center space-y-1 animate-in fade-in">
            <div className="text-xs text-emerald-700 font-medium">设备配对码 (5 分钟有效)</div>
            <div className="text-3xl font-mono font-bold tracking-widest text-emerald-800 select-all">
              {pairCode}
            </div>
            <div className="text-[11px] text-slate-400">
              剩余时间: {Math.floor(expiresIn / 60)} 分 {expiresIn % 60} 秒
            </div>
          </div>
        )}
      </div>

      {/* 已连接设备列表 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
            <Wifi className="h-4 w-4 text-emerald-600" />
            <span>已绑定的设备 ({devices.length})</span>
          </h2>
          <button onClick={loadDevices} className="text-slate-400 hover:text-slate-600">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>

        {devices.length > 0 ? (
          <div className="space-y-2">
            {devices.map((dev) => (
              <div
                key={dev.id}
                className="flex items-center justify-between rounded-xl border border-slate-200 p-3"
              >
                <div>
                  <div className="text-xs font-semibold text-slate-800">{dev.name}</div>
                  <div className="text-[11px] text-slate-400 font-mono">ID: {dev.id}</div>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block h-2 w-2 rounded-full ${
                      dev.is_active ? 'bg-emerald-500' : 'bg-slate-300'
                    }`}
                  />
                  <span className="text-xs text-slate-600">
                    {dev.is_active ? '在线' : '离线'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-slate-400">
            暂无已连接设备。完成第 2 步挂件烧录后在此配对即可。
          </div>
        )}
      </div>
    </div>
  );
};
