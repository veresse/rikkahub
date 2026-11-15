import React, { useState } from 'react';
import { api } from '../api';
import { ImportPreviewReport } from '../types';
import { UploadCloud, CheckCircle2, AlertCircle, Download, FileArchive, Loader2 } from 'lucide-react';

interface BackupViewProps {
  onImportComplete: () => void;
}

export const BackupView: React.FC<BackupViewProps> = ({ onImportComplete }) => {
  const [isUploading, setIsUploading] = useState(false);
  const [previewReport, setPreviewReport] = useState<ImportPreviewReport | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);
  const [importSuccess, setImportSuccess] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setErrorMessage(null);
    setPreviewReport(null);
    setImportSuccess(false);

    try {
      const report = await api.uploadBackupPreview(file);
      setPreviewReport(report);
    } catch (err: any) {
      setErrorMessage(err.message || '备份解析失败');
    } finally {
      setIsUploading(false);
    }
  };

  const handleConfirm = async () => {
    if (!previewReport) return;
    setIsConfirming(true);
    try {
      await api.confirmImport(previewReport.preview_id);
      setImportSuccess(true);
      setPreviewReport(null);
      onImportComplete();
    } catch (err: any) {
      setErrorMessage('数据落库失败: ' + String(err));
    } finally {
      setIsConfirming(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-50 p-4 sm:p-6 overflow-y-auto max-w-4xl mx-auto space-y-6">
      {/* 标题 */}
      <div className="border-b border-slate-200 pb-4">
        <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
          <FileArchive className="h-5 w-5 text-emerald-600" />
          <span>数据备份与迁移中心</span>
        </h1>
        <p className="text-xs text-slate-500 mt-1">
          在此上传手机版 RikkaHub 导出的备份 ZIP 文件，自动无损还原角色、历史对话树与记忆
        </p>
      </div>

      {/* 拖拽上传卡片 */}
      <div className="rounded-2xl border-2 border-dashed border-slate-300 bg-white p-6 sm:p-8 text-center hover:border-emerald-500 transition-colors relative">
        <input
          type="file"
          accept=".zip"
          onChange={handleFileChange}
          disabled={isUploading}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
        />
        <div className="flex flex-col items-center">
          {isUploading ? (
            <Loader2 className="h-10 w-10 text-emerald-600 animate-spin mb-3" />
          ) : (
            <UploadCloud className="h-10 w-10 text-emerald-600 mb-3" />
          )}
          <p className="text-sm font-semibold text-slate-700">
            {isUploading ? '正在校验并安全解析备份中...' : '点击或拖拽手机版备份 ZIP 文件到这里'}
          </p>
          <p className="text-xs text-slate-400 mt-1">
            支持包含 settings.json 与 rikka_hub.db 的完整备份包
          </p>
        </div>
      </div>

      {/* 错误提示 */}
      {errorMessage && (
        <div className="flex items-center gap-2 rounded-xl bg-rose-50 border border-rose-200 p-3.5 text-xs text-rose-700">
          <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* 导入成功提示 */}
      {importSuccess && (
        <div className="flex items-center gap-2 rounded-xl bg-emerald-50 border border-emerald-200 p-3.5 text-xs text-emerald-800">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
          <span>数据已成功无损导入！现在你可以切换到「对话」继续历史会话。</span>
        </div>
      )}

      {/* 阶段 1：预览报告展示 */}
      {previewReport && (
        <div className="rounded-2xl border border-emerald-200 bg-white p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <h2 className="text-sm font-bold text-slate-800 flex items-center gap-1.5">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <span>备份文件解析成功，请确认导入内容</span>
            </h2>
            <span className="text-[11px] text-slate-400 font-mono">
              ID: {previewReport.preview_id.slice(0, 8)}
            </span>
          </div>

          {/* 统计指标卡片 */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl bg-slate-50 border border-slate-200/60 p-3 text-center">
              <div className="text-xs text-slate-500">发现角色</div>
              <div className="text-lg font-bold text-emerald-600 mt-0.5">
                {previewReport.summary.assistant_count}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 border border-slate-200/60 p-3 text-center">
              <div className="text-xs text-slate-500">历史对话</div>
              <div className="text-lg font-bold text-emerald-600 mt-0.5">
                {previewReport.summary.conversation_count}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 border border-slate-200/60 p-3 text-center">
              <div className="text-xs text-slate-500">消息节点/分支</div>
              <div className="text-lg font-bold text-emerald-600 mt-0.5">
                {previewReport.summary.message_node_count} / {previewReport.summary.total_message_versions}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 border border-slate-200/60 p-3 text-center">
              <div className="text-xs text-slate-500">长期记忆条目</div>
              <div className="text-lg font-bold text-emerald-600 mt-0.5">
                {previewReport.summary.memory_count}
              </div>
            </div>
          </div>

          {/* 角色与会话预览 */}
          <div className="text-xs text-slate-600 space-y-1">
            <div>
              <span className="font-semibold text-slate-700">识别到的角色：</span>
              {previewReport.assistants_sample.join('、') || '无'}
            </div>
          </div>

          {/* 不支持项提示 */}
          {previewReport.summary.unsupported_features.length > 0 && (
            <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 text-xs text-amber-900 space-y-1">
              <div className="font-semibold flex items-center gap-1">
                <AlertCircle className="h-3.5 w-3.5 text-amber-600" />
                <span>兼容性温馨提示（已安全旁路，核心人设不受影响）：</span>
              </div>
              <ul className="list-disc list-inside text-[11px] text-amber-800 space-y-0.5">
                {previewReport.summary.unsupported_features.map((item, idx) => (
                  <li key={idx}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 确认操作按钮 */}
          <div className="flex justify-end gap-3 pt-2">
            <button
              onClick={() => setPreviewReport(null)}
              className="rounded-xl px-4 py-2 text-xs text-slate-600 hover:bg-slate-100"
            >
              放弃
            </button>
            <button
              onClick={handleConfirm}
              disabled={isConfirming}
              className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-5 py-2 text-xs font-semibold text-white shadow hover:bg-emerald-700 active:scale-95 disabled:opacity-50 transition-all"
            >
              {isConfirming && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              <span>{isConfirming ? '正在全量落库中...' : '确认并执行导入'}</span>
            </button>
          </div>
        </div>
      )}

      {/* 导出当前服务器数据 */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-bold text-slate-800">导出当前服务器备份</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            将当前服务器上的全部角色、会话、分支树与记忆打包成标准 RikkaHub ZIP 文件下载
          </p>
        </div>
        <a
          href={api.getExportBackupUrl()}
          className="flex items-center gap-1.5 rounded-xl border border-slate-300 bg-white px-4 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 active:scale-95 transition-all shrink-0"
        >
          <Download className="h-4 w-4 text-emerald-600" />
          <span>下载完整备份 (.zip)</span>
        </a>
      </div>
    </div>
  );
};
