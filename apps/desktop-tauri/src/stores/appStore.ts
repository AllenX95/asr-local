import { defineStore } from 'pinia';
import { api } from '../ipc/tauriClient';
import type {
  AppInfo,
  AsrBackend,
  AsrCloudProfile,
  AsrProfilesState,
  HistoryItem,
  JobResult,
  ModelsConfig,
  ReplacementRule,
  RunJobRequest,
  SummaryProfile,
  SummaryProfilesState,
  SummaryTemplate,
  ViewKey,
  WorkerUiEvent
} from '../ipc/workerTypes';

interface WorkerLaneState {
  id: number;
  jobId: string;
  sourcePath: string;
  stage: string;
  detail: string;
  progress: number;
  busy: boolean;
  paused: boolean;
  error: string;
  latestResult: JobResult | null;
}

interface WorkbenchState {
  sourcePath: string;
  outputDir: string;
  outputFileName: string;
  asrBackend: AsrBackend;
  asrProfileName: string;
  languageMode: string;
  fixedLanguage: string;
  speakerDiarization: boolean;
  keepFillers: boolean;
  autoPunctuation: boolean;
  contextText: string;
  termsText: string;
  replacementsText: string;
}

interface MarkdownState {
  path: string;
  content: string;
  savedContent: string;
  largeMode: boolean;
  search: string;
}

interface SummaryState {
  selectedProfileName: string;
  selectedTemplateName: string;
  prompt: string;
  output: string;
  outputPath: string;
  busy: boolean;
  error: string;
}

interface SettingsState {
  models: ModelsConfig | null;
  health: Record<string, unknown> | null;
  healthError: string;
  checkingHealth: boolean;
}

interface AppState {
  activeView: ViewKey;
  initialized: boolean;
  initializing: boolean;
  submitting: boolean;
  appInfo: AppInfo | null;
  statusTitle: string;
  statusDetail: string;
  error: string;
  lanes: Record<number, WorkerLaneState>;
  jobs: WorkerUiEvent[];
  workbench: WorkbenchState;
  markdown: MarkdownState;
  summary: SummaryState;
  settings: SettingsState;
  asrProfiles: AsrProfilesState;
  summaryProfiles: SummaryProfilesState;
  summaryTemplates: SummaryTemplate[];
  history: HistoryItem[];
  workerUnlisten: null | (() => void);
}

const defaultLane = (id: number): WorkerLaneState => ({
  id,
  jobId: '',
  sourcePath: '',
  stage: 'idle',
  detail: '等待任务。',
  progress: 0,
  busy: false,
  paused: false,
  error: '',
  latestResult: null
});

const defaultWorkbench = (): WorkbenchState => ({
  sourcePath: '',
  outputDir: '',
  outputFileName: '',
  asrBackend: 'local',
  asrProfileName: '',
  languageMode: 'auto',
  fixedLanguage: 'zh',
  speakerDiarization: true,
  keepFillers: true,
  autoPunctuation: true,
  contextText: '',
  termsText: '',
  replacementsText: ''
});

export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    activeView: 'workbench',
    initialized: false,
    initializing: false,
    submitting: false,
    appInfo: null,
    statusTitle: '准备就绪',
    statusDetail: '选择音频文件后开始本地转写。',
    error: '',
    lanes: {
      1: defaultLane(1),
      2: defaultLane(2)
    },
    jobs: [],
    workbench: defaultWorkbench(),
    markdown: {
      path: '',
      content: '',
      savedContent: '',
      largeMode: false,
      search: ''
    },
    summary: {
      selectedProfileName: '',
      selectedTemplateName: '',
      prompt: '',
      output: '',
      outputPath: '',
      busy: false,
      error: ''
    },
    settings: {
      models: null,
      health: null,
      healthError: '',
      checkingHealth: false
    },
    asrProfiles: {
      profiles: [],
      last_profile: null
    },
    summaryProfiles: {
      profiles: [],
      last_profile: null
    },
    summaryTemplates: [],
    history: [],
    workerUnlisten: null
  }),
  getters: {
    markdownDirty: (state) => state.markdown.content !== state.markdown.savedContent,
    activeProfile: (state): SummaryProfile | null =>
      state.summaryProfiles.profiles.find(
        (profile) => profile.name === state.summary.selectedProfileName
      ) ?? null,
    activeAsrProfile: (state): AsrCloudProfile | null =>
      state.asrProfiles.profiles.find(
        (profile) => profile.name === state.workbench.asrProfileName
      ) ?? null,
    activeTemplate: (state): SummaryTemplate | null =>
      state.summaryTemplates.find(
        (template) => template.name === state.summary.selectedTemplateName
      ) ?? null,
    workerBusy: (state) => Object.values(state.lanes).some((lane) => lane.busy)
  },
  actions: {
    async initialize() {
      if (this.initialized || this.initializing) {
        return;
      }

      this.initializing = true;
      try {
        // Worker events are ephemeral, so subscribe before any potentially slow disk scans.
        await this.listenToWorker();
        this.appInfo = await api.appInfo();
        this.workbench.outputDir = this.appInfo.outputs_dir;
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
        if (failures.length > 0) {
          this.setError('部分初始化数据加载失败', failures.join('\n'));
        }
      } catch (error) {
        this.setError('应用初始化失败', error);
      } finally {
        this.initializing = false;
      }
    },

    async listenToWorker() {
      if (this.workerUnlisten) {
        return;
      }
      this.workerUnlisten = await api.listenWorkerEvents((event) => {
        this.applyWorkerEvent(event);
      });
    },

    setActiveView(view: ViewKey) {
      this.activeView = view;
    },

    setStatus(title: string, detail = '') {
      this.statusTitle = title;
      this.statusDetail = detail;
      this.error = '';
    },

    setError(title: string, detail: unknown) {
      this.statusTitle = title;
      this.statusDetail = String(detail);
      this.error = String(detail);
    },

    async chooseAudioFile() {
      const path = await api.selectAudioFile();
      if (!path) {
        return;
      }
      this.workbench.sourcePath = path;
      this.workbench.outputFileName = defaultTranscriptName(path);
      if (!this.workbench.outputDir && this.appInfo) {
        this.workbench.outputDir = this.appInfo.outputs_dir;
      }
    },

    async chooseOutputDir() {
      const path = await api.selectOutputDir();
      if (path) {
        this.workbench.outputDir = path;
      }
    },

    async submitTranscription() {
      if (!this.initialized || this.submitting) {
        return;
      }

      const sourcePath = this.workbench.sourcePath.trim();
      const outputDir = this.workbench.outputDir.trim() || this.appInfo?.outputs_dir || '';
      const outputFileName =
        normalizeMarkdownName(this.workbench.outputFileName) || defaultTranscriptName(sourcePath);

      if (!sourcePath) {
        this.setError('缺少音频输入', '请先选择本地音频文件。');
        return;
      }
      if (!outputDir) {
        this.setError('缺少输出目录', '请先选择输出目录。');
        return;
      }
      const asrProfile = this.workbench.asrBackend === 'cloud' ? this.activeAsrProfile : null;
      if (this.workbench.asrBackend === 'cloud' && !asrProfile) {
        this.setError('缺少云端 ASR Profile', '请先在设置页保存并选择一个云端 ASR Profile。');
        return;
      }

      const request: RunJobRequest = {
        job_id: `tauri_${Date.now()}`,
        source_path: sourcePath,
        output_dir: outputDir,
        output_file_name: outputFileName,
        asr_backend: this.workbench.asrBackend,
        cloud_asr_profile: asrProfile,
        language_mode: this.workbench.languageMode,
        fixed_language:
          this.workbench.languageMode === 'fixed' ? this.workbench.fixedLanguage.trim() || null : null,
        enable_speaker_diarization: this.workbench.speakerDiarization,
        context_text: this.workbench.contextText,
        terms: parseLines(this.workbench.termsText),
        replacements: parseReplacements(this.workbench.replacementsText),
        keep_fillers: this.workbench.keepFillers,
        auto_punctuation: this.workbench.autoPunctuation
      };

      this.setStatus(
        '正在提交转写任务',
        this.workbench.asrBackend === 'cloud'
          ? '任务会进入 Python worker 队列，并调用云端 ASR API。'
          : '任务会进入本地 Python worker 队列。'
      );
      this.submitting = true;
      try {
        await this.listenToWorker();
        const response = await api.submitJob(request);
        const lane = this.lanes[response.lane_id] ?? defaultLane(response.lane_id);
        if (lane.jobId !== response.job_id) {
          lane.jobId = response.job_id;
          lane.sourcePath = request.source_path;
          lane.stage = 'queued';
          lane.detail =
            response.queued_ahead > 0
              ? `前方还有 ${response.queued_ahead} 个任务。`
              : '等待 Python worker 开始处理。';
          lane.progress = 0;
          lane.busy = true;
          lane.paused = false;
          lane.error = '';
          this.lanes[response.lane_id] = lane;
          this.setStatus('任务已排队', `已分配到 Worker ${response.lane_id}。`);
        }
      } catch (error) {
        this.setError('转写任务提交失败', error);
      } finally {
        this.submitting = false;
      }
    },

    applyWorkerEvent(event: WorkerUiEvent) {
      const lane = this.lanes[event.lane_id] ?? defaultLane(event.lane_id);
      lane.jobId = event.job_id;
      lane.sourcePath = event.source_path;
      lane.stage = event.stage;
      lane.detail = event.error || event.detail;
      lane.progress = Math.max(0, Math.min(1, event.progress));
      lane.busy = event.event !== 'completed' && event.event !== 'failed';
      lane.paused = event.stage === 'paused' ? true : event.stage === 'resumed' ? false : lane.paused;
      lane.error = event.error ?? '';
      this.lanes[event.lane_id] = lane;
      this.jobs.unshift(event);
      this.jobs = this.jobs.slice(0, 80);

      if (event.event === 'failed') {
        this.setError('转写失败', event.error || event.detail);
      } else {
        this.setStatus(stageTitle(event.stage), event.detail);
      }

      if (event.event === 'completed' && event.result) {
        lane.latestResult = event.result;
        void this.finishCompletedJob(event.result);
      }
    },

    async finishCompletedJob(result: JobResult) {
      try {
        const file = await api.readTextFile(result.md_path);
        this.setMarkdown(file.path, file.content, true);
        this.summary.outputPath = defaultSummaryName(result.md_path);
        this.setStatus(
          '转写已完成',
          `已生成 ${result.segments} 段、${result.speakers} 位说话人。`
        );
        this.activeView = 'markdown';
        await this.refreshHistory();
      } catch (error) {
        this.setError('转写完成，但 Markdown 加载失败', error);
      }
    },

    async pauseLane(id: number) {
      try {
        await api.pauseLane(id);
        this.lanes[id].paused = true;
      } catch (error) {
        this.setError('暂停失败', error);
      }
    },

    async resumeLane(id: number) {
      try {
        await api.resumeLane(id);
        this.lanes[id].paused = false;
      } catch (error) {
        this.setError('恢复失败', error);
      }
    },

    async terminateLane(id: number) {
      try {
        await api.terminateLane(id);
        this.lanes[id].detail = '已请求终止当前任务。';
      } catch (error) {
        this.setError('终止失败', error);
      }
    },

    async openMarkdownFile() {
      const path = await api.selectMarkdownFile();
      if (path) {
        await this.loadMarkdownPath(path);
      }
    },

    async loadLatestWorkerResult(laneId: number) {
      const result = this.lanes[laneId]?.latestResult;
      if (!result) {
        this.setError(`Worker ${laneId} 暂无识别结果`, '请等待该 Worker 完成一次转写任务。');
        return;
      }

      await this.loadMarkdownPath(result.md_path, false);
      if (this.markdown.path === result.md_path) {
        this.setStatus(`已选择 Worker ${laneId} 的最新结果`, result.md_path);
      }
    },

    async loadMarkdownPath(path: string, activateView = true) {
      try {
        const file = await api.readTextFile(path);
        this.setMarkdown(file.path, file.content, true);
        this.summary.outputPath = defaultSummaryName(file.path);
        if (activateView) {
          this.activeView = 'markdown';
        }
      } catch (error) {
        this.setError('Markdown 打开失败', error);
      }
    },

    setMarkdown(path: string, content: string, markSaved = false) {
      this.markdown.path = path;
      this.markdown.content = content;
      this.markdown.largeMode = content.length >= 2 * 1024 * 1024;
      if (markSaved) {
        this.markdown.savedContent = content;
      }
    },

    async saveMarkdown() {
      if (!this.markdown.path.trim()) {
        this.setError('缺少保存路径', '请先在路径输入框中填写 Markdown 保存路径。');
        return;
      }
      try {
        const saved = await api.saveTextFile(this.markdown.path, this.markdown.content);
        this.markdown.path = saved.path;
        this.markdown.savedContent = this.markdown.content;
        this.setStatus('Markdown 已保存', saved.path);
        await this.refreshHistory();
      } catch (error) {
        this.setError('Markdown 保存失败', error);
      }
    },

    async refreshSummaryProfiles() {
      this.summaryProfiles = await api.loadSummaryProfiles();
      const selected = this.summaryProfiles.last_profile || this.summaryProfiles.profiles[0]?.name || '';
      this.summary.selectedProfileName = selected;
    },

    async refreshSummaryTemplates() {
      this.summaryTemplates = await api.loadSummaryTemplates();
      const selected = this.summaryTemplates[0]?.name || '';
      this.summary.selectedTemplateName = selected;
      this.summary.prompt = this.summaryTemplates[0]?.prompt || '';
    },

    applyTemplate(name: string) {
      this.summary.selectedTemplateName = name;
      const template = this.summaryTemplates.find((item) => item.name === name);
      if (template) {
        this.summary.prompt = template.prompt;
      }
    },

    async generateSummary() {
      const profile = this.activeProfile;
      const prompt = this.summary.prompt.trim();
      const markdown = this.markdown.content.trim();
      if (!profile) {
        this.setError('缺少总结 Profile', '请先在设置页保存 OpenAI 兼容 API 配置。');
        return;
      }
      if (!prompt) {
        this.setError('缺少 Prompt', '请选择模板或输入自定义 Prompt。');
        return;
      }
      if (!markdown) {
        this.setError('缺少输入稿', '请先打开或生成一份 Markdown 转写稿。');
        return;
      }

      this.summary.busy = true;
      this.summary.error = '';
      this.setStatus('正在生成总结', 'Markdown 内容会发送到当前配置的 API。');
      try {
        this.summary.output = await api.generateSummary({
          base_url: profile.base_url,
          api_key: profile.api_key,
          model: profile.model,
          prompt,
          transcript_markdown: markdown
        });
        if (!this.summary.outputPath) {
          this.summary.outputPath = defaultSummaryName(this.markdown.path);
        }
        this.setStatus('总结已生成', '可以继续编辑或导出 Markdown。');
      } catch (error) {
        this.summary.error = String(error);
        this.setError('总结生成失败', error);
      } finally {
        this.summary.busy = false;
      }
    },

    async saveSummary() {
      const path = this.summary.outputPath.trim();
      if (!path) {
        this.setError('缺少总结保存路径', '请先填写总结 Markdown 输出路径。');
        return;
      }
      try {
        const saved = await api.saveTextFile(path, this.summary.output);
        this.summary.outputPath = saved.path;
        this.setStatus('总结已保存', saved.path);
        await this.refreshHistory();
      } catch (error) {
        this.setError('总结保存失败', error);
      }
    },

    async refreshModels() {
      try {
        this.settings.models = await api.loadModelsConfig();
      } catch (error) {
        this.setError('模型配置读取失败', error);
      }
    },

    async refreshAsrProfiles() {
      this.asrProfiles = await api.loadAsrProfiles();
      const selected = this.asrProfiles.last_profile || this.asrProfiles.profiles[0]?.name || '';
      if (!this.workbench.asrProfileName || !this.asrProfiles.profiles.some((profile) => profile.name === this.workbench.asrProfileName)) {
        this.workbench.asrProfileName = selected;
      }
    },

    async saveModelPaths(modelRoot: string, qwenPath: string, pyannotePath: string) {
      try {
        this.settings.models = await api.saveModelPaths(modelRoot, qwenPath, pyannotePath);
        this.setStatus('模型配置已保存', this.settings.models.config_path);
      } catch (error) {
        this.setError('模型配置保存失败', error);
      }
    },

    async saveAsrProfile(profile: AsrCloudProfile) {
      try {
        this.asrProfiles = await api.saveAsrProfile(profile);
        this.workbench.asrProfileName =
          this.asrProfiles.last_profile || this.asrProfiles.profiles[0]?.name || profile.name;
        this.setStatus('云端 ASR Profile 已保存', this.workbench.asrProfileName);
      } catch (error) {
        this.setError('云端 ASR Profile 保存失败', error);
      }
    },

    async deleteAsrProfile(name: string) {
      try {
        this.asrProfiles = await api.deleteAsrProfile(name);
        this.workbench.asrProfileName =
          this.asrProfiles.last_profile || this.asrProfiles.profiles[0]?.name || '';
      } catch (error) {
        this.setError('云端 ASR Profile 删除失败', error);
      }
    },

    async checkWorkerHealth() {
      this.settings.checkingHealth = true;
      this.settings.healthError = '';
      try {
        this.settings.health = await api.workerHealthCheck();
        this.setStatus('Worker 检测完成', 'Python 运行时可用。');
      } catch (error) {
        this.settings.health = null;
        this.settings.healthError = String(error);
        this.setError('Worker 检测失败', error);
      } finally {
        this.settings.checkingHealth = false;
      }
    },

    async saveProfile(profile: SummaryProfile) {
      try {
        this.summaryProfiles = await api.saveSummaryProfile(profile);
        this.summary.selectedProfileName = profile.name;
        this.setStatus('总结 Profile 已保存', profile.name);
      } catch (error) {
        this.setError('总结 Profile 保存失败', error);
      }
    },

    async deleteProfile(name: string) {
      try {
        this.summaryProfiles = await api.deleteSummaryProfile(name);
        this.summary.selectedProfileName =
          this.summaryProfiles.last_profile || this.summaryProfiles.profiles[0]?.name || '';
      } catch (error) {
        this.setError('总结 Profile 删除失败', error);
      }
    },

    async saveTemplate(name: string, prompt: string) {
      try {
        this.summaryTemplates = await api.saveSummaryTemplate(name, prompt);
        this.applyTemplate(name);
        this.setStatus('总结模板已保存', name);
      } catch (error) {
        this.setError('总结模板保存失败', error);
      }
    },

    async deleteTemplate(name: string) {
      try {
        this.summaryTemplates = await api.deleteSummaryTemplate(name);
        this.applyTemplate(this.summaryTemplates[0]?.name || '');
      } catch (error) {
        this.setError('总结模板删除失败', error);
      }
    },

    async refreshHistory() {
      this.history = await api.listHistoryItems(100);
    },

    async openPath(path: string) {
      try {
        await api.openPath(path);
      } catch (error) {
        this.setError('打开路径失败', error);
      }
    }
  }
});

function parseLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function parseReplacements(value: string): ReplacementRule[] {
  return parseLines(value)
    .map((line) => {
      const separator = line.includes('=>') ? '=>' : line.includes('->') ? '->' : ',';
      const [wrong, correct] = line.split(separator).map((part) => part.trim());
      return { wrong: wrong || '', correct: correct || '' };
    })
    .filter((rule) => rule.wrong && rule.correct);
}

function normalizeMarkdownName(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return '';
  }
  return /\.(md|markdown)$/i.test(trimmed) ? trimmed : `${trimmed}.md`;
}

function defaultTranscriptName(path: string): string {
  const name = baseName(path);
  const stem = name.replace(/\.[^.]+$/, '') || 'transcript';
  return `${stem}.transcript.md`;
}

function defaultSummaryName(path: string): string {
  if (!path) {
    return '';
  }
  const dir = dirName(path);
  const name = baseName(path);
  const stem = name
    .replace(/\.transcript\.md$/i, '')
    .replace(/\.md$/i, '')
    .replace(/\.markdown$/i, '');
  return joinPath(dir, `${stem || 'transcript'}.summary.md`);
}

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function dirName(path: string): string {
  const normalized = path.replace(/\\/g, '/');
  const index = normalized.lastIndexOf('/');
  return index >= 0 ? normalized.slice(0, index) : '';
}

function joinPath(dir: string, name: string): string {
  if (!dir) {
    return name;
  }
  return `${dir.replace(/[\\/]$/, '')}/${name}`;
}

function stageTitle(stage: string): string {
  const labels: Record<string, string> = {
    queued: '任务已排队',
    worker_starting: 'Worker 启动中',
    preparing: '准备运行环境',
    decoding: '解析音频',
    diarizing: '执行说话人分离',
    segmenting: '整理说话片段',
    transcribing: '执行 ASR 转写',
    merging: '合并转写结果',
    normalizing: '应用后处理',
    exporting: '写出结果文件',
    paused: '任务已暂停',
    resumed: '任务已恢复',
    terminating: '正在终止',
    completed: '转写完成',
    failed: '任务失败'
  };
  return labels[stage] || '执行中';
}
