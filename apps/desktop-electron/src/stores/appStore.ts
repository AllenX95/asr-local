import { defineStore } from 'pinia';
import { api } from '../ipc/desktopClient';
import type {
  AppInfo,
  AsrCloudProfile,
  AsrProfilesState,
  HistoryItem,
  ModelsConfig,
  SummaryProfile,
  SummaryProfilesState,
  SummaryTemplate,
  ViewKey
} from '../ipc/workerTypes';

interface AppState {
  activeView: ViewKey;
  initialized: boolean;
  initializing: boolean;
  appInfo: AppInfo | null;
  statusTitle: string;
  statusDetail: string;
  error: string;
  workbench: { asrProfileName: string };
  markdown: { path: string; content: string; savedContent: string; largeMode: boolean; search: string };
  summary: { selectedProfileName: string; selectedTemplateName: string; prompt: string };
  settings: { models: ModelsConfig | null; health: Record<string, unknown> | null; healthError: string; checkingHealth: boolean };
  asrProfiles: AsrProfilesState;
  summaryProfiles: SummaryProfilesState;
  summaryTemplates: SummaryTemplate[];
  history: HistoryItem[];
}

export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    activeView: 'workflow',
    initialized: false,
    initializing: false,
    appInfo: null,
    statusTitle: '准备就绪',
    statusDetail: '选择音频文件后开始本地工作流。',
    error: '',
    workbench: { asrProfileName: '' },
    markdown: { path: '', content: '', savedContent: '', largeMode: false, search: '' },
    summary: { selectedProfileName: '', selectedTemplateName: '', prompt: '' },
    settings: { models: null, health: null, healthError: '', checkingHealth: false },
    asrProfiles: { profiles: [], last_profile: null },
    summaryProfiles: { profiles: [], last_profile: null },
    summaryTemplates: [],
    history: []
  }),
  getters: {
    markdownDirty: (state) => state.markdown.content !== state.markdown.savedContent
  },
  actions: {
    async initialize() {
      if (this.initialized || this.initializing) return;
      this.initializing = true;
      try {
        this.appInfo = await api.appInfo();
        this.initialized = true;
        const results = await Promise.allSettled([
          this.refreshModels(),
          this.refreshAsrProfiles(),
          this.refreshSummaryProfiles(),
          this.refreshSummaryTemplates(),
          this.refreshHistory()
        ]);
        const failures = results
          .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
          .map((result) => String(result.reason));
        if (failures.length) this.setError('部分初始化数据加载失败', failures.join('\n'));
        void this.refreshRuntimeHealth();
      } catch (error) {
        this.setError('应用初始化失败', error);
      } finally {
        this.initializing = false;
      }
    },

    setActiveView(view: ViewKey) { this.activeView = view; },
    setStatus(title: string, detail = '') { this.statusTitle = title; this.statusDetail = detail; this.error = ''; },
    setError(title: string, detail: unknown) { this.statusTitle = title; this.statusDetail = String(detail); this.error = String(detail); },

    async openMarkdownFile() {
      const filePath = await api.selectMarkdownFile();
      if (filePath) await this.loadMarkdownPath(filePath);
    },
    async loadMarkdownPath(filePath: string, activateView = true) {
      try {
        const file = await api.readTextFile(filePath);
        this.setMarkdown(file.path, file.content, true);
        if (activateView) this.activeView = 'markdown';
      } catch (error) { this.setError('Markdown 打开失败', error); }
    },
    setMarkdown(filePath: string, content: string, markSaved = false) {
      this.markdown.path = filePath;
      this.markdown.content = content;
      this.markdown.largeMode = content.length >= 2 * 1024 * 1024;
      if (markSaved) this.markdown.savedContent = content;
    },
    async saveMarkdown() {
      if (!this.markdown.path.trim()) { this.setError('缺少保存路径', '请先填写 Markdown 保存路径。'); return; }
      try {
        const saved = await api.saveTextFile(this.markdown.path, this.markdown.content);
        this.markdown.path = saved.path;
        this.markdown.savedContent = this.markdown.content;
        this.setStatus('Markdown 已保存', saved.path);
        await this.refreshHistory();
      } catch (error) { this.setError('Markdown 保存失败', error); }
    },

    async refreshModels() { this.settings.models = await api.loadModelsConfig(); },
    async refreshRuntimeHealth() {
      try {
        this.settings.health = await api.workerHealthCheck();
        this.settings.healthError = '';
      } catch (error) {
        this.settings.health = null;
        this.settings.healthError = String(error);
      }
    },
    async refreshAsrProfiles() {
      this.asrProfiles = await api.loadAsrProfiles();
      const selected = this.asrProfiles.last_profile || this.asrProfiles.profiles[0]?.name || '';
      if (!this.workbench.asrProfileName || !this.asrProfiles.profiles.some((profile) => profile.name === this.workbench.asrProfileName)) this.workbench.asrProfileName = selected;
    },
    async refreshSummaryProfiles() {
      this.summaryProfiles = await api.loadSummaryProfiles();
      this.summary.selectedProfileName = this.summaryProfiles.last_profile || this.summaryProfiles.profiles[0]?.name || '';
    },
    async refreshSummaryTemplates() {
      this.summaryTemplates = await api.loadSummaryTemplates();
      this.applyTemplate(this.summaryTemplates[0]?.name || '');
    },
    applyTemplate(name: string) {
      this.summary.selectedTemplateName = name;
      this.summary.prompt = this.summaryTemplates.find((item) => item.name === name)?.prompt || '';
    },
    async saveModelPaths(modelRoot: string, qwen: string, pyannote: string) {
      try { this.settings.models = await api.saveModelPaths(modelRoot, qwen, pyannote); this.setStatus('模型配置已保存', this.settings.models.config_path); }
      catch (error) { this.setError('模型配置保存失败', error); }
    },
    async saveAsrProfile(profile: AsrCloudProfile) {
      try { this.asrProfiles = await api.saveAsrProfile(profile); this.workbench.asrProfileName = this.asrProfiles.last_profile || profile.name; this.setStatus('云端 ASR Profile 已保存', profile.name); }
      catch (error) { this.setError('云端 ASR Profile 保存失败', error); }
    },
    async deleteAsrProfile(name: string) {
      try { this.asrProfiles = await api.deleteAsrProfile(name); this.workbench.asrProfileName = this.asrProfiles.last_profile || this.asrProfiles.profiles[0]?.name || ''; }
      catch (error) { this.setError('云端 ASR Profile 删除失败', error); }
    },
    async saveProfile(profile: SummaryProfile) {
      try { this.summaryProfiles = await api.saveSummaryProfile(profile); this.summary.selectedProfileName = profile.name; this.setStatus('总结 Profile 已保存', profile.name); }
      catch (error) { this.setError('总结 Profile 保存失败', error); }
    },
    async deleteProfile(name: string) {
      try { this.summaryProfiles = await api.deleteSummaryProfile(name); this.summary.selectedProfileName = this.summaryProfiles.last_profile || this.summaryProfiles.profiles[0]?.name || ''; }
      catch (error) { this.setError('总结 Profile 删除失败', error); }
    },
    async saveTemplate(name: string, prompt: string) {
      try { this.summaryTemplates = await api.saveSummaryTemplate(name, prompt); this.applyTemplate(name); this.setStatus('总结模板已保存', name); }
      catch (error) { this.setError('总结模板保存失败', error); }
    },
    async deleteTemplate(name: string) {
      try { this.summaryTemplates = await api.deleteSummaryTemplate(name); this.applyTemplate(this.summaryTemplates[0]?.name || ''); }
      catch (error) { this.setError('总结模板删除失败', error); }
    },
    async checkWorkerHealth() {
      this.settings.checkingHealth = true;
      this.settings.healthError = '';
      try { this.settings.health = await api.workerHealthCheck(); this.setStatus('Runtime 检测完成', 'Python Workflow Runtime v2 可用。'); }
      catch (error) { this.settings.health = null; this.settings.healthError = String(error); this.setError('Runtime 检测失败', error); }
      finally { this.settings.checkingHealth = false; }
    },
    async refreshHistory() { this.history = await api.listHistoryItems(100); },
    async openPath(filePath: string) { try { await api.openPath(filePath); } catch (error) { this.setError('打开路径失败', error); } }
  }
});
