<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ChevronDown, ChevronRight, Clock3, FolderOpen, Pause, Play, RefreshCw, RotateCcw, Square, Trash2 } from '@lucide/vue';
import { api } from '../../ipc/desktopClient';
import type { PipelineProfile, WorkflowCatalogs, WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../../ipc/workerTypes';
import { useAppStore } from '../../stores/appStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import type { WorkflowDraft, WorkflowSnapshot } from '../../workflows/types';

const appStore = useAppStore();
const workflowStore = useWorkflowStore();

const sourcePath = ref('');
const outputDir = ref('');
const baseName = ref('meeting');
const recordingBackground = ref('');
const hotwordsText = ref('');
const extraInstruction = ref('');
const devicePolicy = ref<'auto' | 'cpu' | 'cuda'>('auto');
const pipelineProfile = ref<Exclude<PipelineProfile, 'cloud_asr'>>('pyannote_qwen3_asr');
const selectedProfileName = ref('');
const selectedTemplateName = ref('');
const privacyConfirmed = ref(false);
const error = ref('');
const submitting = ref(false);
const catalogs = ref<WorkflowCatalogs>({ summary_profiles: [], summary_templates: [] });
const editingArtifactId = ref<string | null>(null);
const artifactText = ref('');
const artifactError = ref('');
const artifactSaving = ref(false);
const refreshing = ref(false);
const clearingWorkflowId = ref<string | null>(null);
const recentExpanded = ref(true);
const diagnosticsExpanded = ref(false);
const pyannoteReady = computed(() => appStore.settings.models?.pyannote_exists ?? false);
const qwenReady = computed(() => appStore.settings.models?.qwen_exists ?? false);
const runtimeReadiness = computed(() => (appStore.settings.health?.model_readiness ?? null) as Record<string, any> | null);
const qwenRuntimeReady = computed(() => runtimeReadiness.value ? Boolean(runtimeReadiness.value.qwen?.runtime_ready) : true);

const availableProfiles = computed<WorkflowSummaryProfile[]>(() => {
  if (catalogs.value.summary_profiles.length) return catalogs.value.summary_profiles;
  return appStore.summaryProfiles.profiles.map((profile) => ({
    id: profile.id || `summary-profile-${profile.name}`,
    version: profile.version || 1,
    name: profile.name,
    base_url: profile.base_url,
    model: profile.model,
    auth_mode: profile.api_key.trim() ? 'bearer' : 'none',
    provider_binding_sha256: `catalog:${profile.id}:v${profile.version}`,
  }));
});
const availableTemplates = computed<WorkflowSummaryTemplate[]>(() => {
  if (catalogs.value.summary_templates.length) return catalogs.value.summary_templates;
  return appStore.summaryTemplates.map((template) => ({
    id: template.id || `summary-template-${template.name}`,
    version: template.version || 1,
    name: template.name,
    prompt: template.prompt,
  }));
});
const selectedProfile = computed(() =>
  availableProfiles.value.find((profile) => profile.name === selectedProfileName.value) ?? null,
);
const selectedTemplate = computed(() =>
  availableTemplates.value.find((template) => template.name === selectedTemplateName.value) ?? null,
);
const providerAuthorizationText = computed(() => {
  const notices: string[] = [];
  if (selectedProfile.value) notices.push(`总结文本将发送到 ${selectedProfile.value.base_url}，使用模型 ${selectedProfile.value.model}`);
  return notices.length ? `${notices.join('；')}。` : '';
});
const pipelineLabel = computed(() => 'Qwen3-ASR');

watch(selectedProfileName, () => {
  privacyConfirmed.value = false;
});
const selectedWorkflow = computed<WorkflowSnapshot | null>(() => {
  const id = workflowStore.selectedWorkflowId;
  return id ? workflowStore.workflowsById[id] ?? null : workflowStore.workflows[0] ?? null;
});
const activeStatuses = new Set(['queued', 'running', 'paused', 'waiting_for_secret']);
const activeWorkflows = computed(() => workflowStore.workflows.filter((item) => activeStatuses.has(item.status)));
const recentWorkflows = computed(() => workflowStore.workflows.filter((item) => !activeStatuses.has(item.status)));
const runningCount = computed(() => activeWorkflows.value.filter((item) => ['running', 'paused', 'waiting_for_secret'].includes(item.status)).length);
const queuedCount = computed(() => activeWorkflows.value.filter((item) => item.status === 'queued').length);

watch(
  () => appStore.initialized,
  (initialized) => {
    if (!initialized) return;
    if (!outputDir.value) outputDir.value = appStore.appInfo?.outputs_dir || 'outputs';
    void api.loadWorkflowCatalogs().then((value) => {
      catalogs.value = value;
      if (!selectedProfileName.value) selectedProfileName.value = value.summary_profiles[0]?.name || '';
      if (!selectedTemplateName.value) selectedTemplateName.value = value.summary_templates[0]?.name || '';
    }).catch((reason) => {
      error.value = String(reason);
    });
    if (!selectedProfileName.value) selectedProfileName.value = availableProfiles.value[0]?.name || '';
    if (!selectedTemplateName.value) selectedTemplateName.value = availableTemplates.value[0]?.name || '';
  },
  { immediate: true },
);

function parseHotwords(): string[] {
  return hotwordsText.value
    .split(/[\n,，]/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseBaseName(path: string): string {
  const file = path.split(/[\\/]/u).pop() || 'meeting';
  return file.replace(/\.[^.]+$/u, '') || 'meeting';
}

async function chooseAudio(): Promise<void> {
  const selected = await api.selectAudioFile();
  if (selected) {
    sourcePath.value = selected;
    if (baseName.value === 'meeting') baseName.value = parseBaseName(selected);
  }
}

async function chooseOutputDir(): Promise<void> {
  const selected = await api.selectOutputDir();
  if (selected) outputDir.value = selected;
}

function buildDraft(): WorkflowDraft {
  const profile = selectedProfile.value;
  const template = selectedTemplate.value;
  if (!profile) throw new Error('请先在设置中创建一个总结模型 Profile。');
  if (!template) throw new Error('请先在设置中创建一个总结模板。');
  if (appStore.initialized && !pyannoteReady.value) throw new Error('未检测到 Pyannote 模型，请先在设置中配置模型路径。');
  if (appStore.initialized && (!qwenReady.value || !qwenRuntimeReady.value)) throw new Error('Qwen3-ASR 模型或 Python inference runtime 不可用，请检查依赖和模型路径。');
  const authMode = profile.auth_mode;
  return {
    draft_version: 2,
    display_name: baseName.value.trim() || 'meeting',
    source: { path: sourcePath.value.trim() },
    transcription: {
      pipeline_profile: pipelineProfile.value,
      pipeline_profile_version: 1,
      device_policy: devicePolicy.value,
      audio: {
        channel_strategy: 'mixdown',
      },
      language: { mode: 'auto', value: null },
      prompt_input: {
        recording_background: recordingBackground.value,
        hotwords: parseHotwords(),
        extra_instruction: extraInstruction.value,
      },
      postprocess: { replacements: [], keep_fillers: true, auto_punctuation: true },
      cloud_profile: null,
    },
    summary: {
      profile_id: profile.id,
      profile_version: profile.version,
      base_url: profile.base_url,
      auth_mode: authMode,
      model: profile.model,
      model_source: 'profile_default',
      credential_ref: authMode === 'bearer' ? `summary:${profile.id}` : null,
      provider_binding_sha256: profile.provider_binding_sha256,
      template: {
        id: template.id,
        version: template.version,
        name: template.name,
        prompt_snapshot: template.prompt,
      },
      context_strategy: 'auto',
      input_token_budget: 8000,
      max_output_tokens: 2000,
    },
    output: {
      directory: outputDir.value.trim() || 'outputs',
      base_name: baseName.value.trim() || 'meeting',
      collision_policy: 'unique_suffix',
    },
  };
}

async function submit(): Promise<void> {
  error.value = '';
  if (!sourcePath.value.trim()) {
    error.value = '请选择音频文件。';
    return;
  }
  if (!selectedProfile.value || !privacyConfirmed.value) {
    error.value = '请确认转录文本将发送到所选总结服务后再启动。';
    return;
  }
  submitting.value = true;
  try {
    await workflowStore.submit(buildDraft());
  } catch (reason) {
    error.value = String(reason);
  } finally {
    submitting.value = false;
  }
}

function canPause(snapshot: WorkflowSnapshot): boolean {
  return snapshot.status === 'running';
}

function canResume(snapshot: WorkflowSnapshot): boolean {
  return snapshot.status === 'paused';
}

function canCancel(snapshot: WorkflowSnapshot): boolean {
  return snapshot.status === 'queued' || snapshot.status === 'running' || snapshot.status === 'paused';
}

async function control(action: 'pause' | 'resume' | 'cancel'): Promise<void> {
  const snapshot = selectedWorkflow.value;
  if (!snapshot) return;
  try {
    await workflowStore.control(snapshot.workflow_id, snapshot.attempt.attempt_id, action);
  } catch (reason) {
    error.value = String(reason);
  }
}

async function retry(): Promise<void> {
  const snapshot = selectedWorkflow.value;
  if (!snapshot) return;
  try {
    await workflowStore.retry(
      snapshot.workflow_id,
      snapshot.attempt.attempt_id,
      snapshot.sequence,
      snapshot.recovery.recommended_retry_stage === 'summarizing' ? 'summarizing' : 'auto',
    );
  } catch (reason) {
    error.value = String(reason);
  }
}

async function refresh(): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await workflowStore.refresh();
  } catch (reason) {
    error.value = String(reason);
  } finally {
    refreshing.value = false;
  }
}

async function clearWorkflow(snapshot: WorkflowSnapshot): Promise<void> {
  if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(snapshot.status)) return;
  if (!window.confirm(`清除“${snapshot.spec.display_name}”的任务记录？\n\n已生成的转录、总结和其他输出文件会保留。`)) return;
  clearingWorkflowId.value = snapshot.workflow_id;
  try {
    await workflowStore.clear(snapshot.workflow_id);
  } catch (reason) {
    error.value = String(reason);
  } finally {
    clearingWorkflowId.value = null;
  }
}

async function editArtifact(artifactId: string): Promise<void> {
  const snapshot = selectedWorkflow.value;
  const artifact = snapshot?.artifacts.find((item) => item.artifact_id === artifactId);
  if (!artifact || !['transcript_markdown', 'final_summary_markdown'].includes(artifact.kind)) return;
  artifactError.value = '';
  try {
    const file = await api.readTextFile(artifact.path);
    editingArtifactId.value = artifact.artifact_id;
    artifactText.value = file.content;
  } catch (reason) {
    artifactError.value = String(reason);
  }
}

function stagingPath(path: string): string {
  const slash = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
  const directory = slash >= 0 ? path.slice(0, slash) : '.';
  return `${directory}/.staging/edit-${crypto.randomUUID()}.md`;
}

async function saveArtifactRevision(): Promise<void> {
  const snapshot = selectedWorkflow.value;
  const artifact = snapshot?.artifacts.find((item) => item.artifact_id === editingArtifactId.value);
  if (!snapshot || !artifact) return;
  artifactSaving.value = true;
  artifactError.value = '';
  try {
    const stagedPath = stagingPath(artifact.path);
    await api.saveTextFile(stagedPath, artifactText.value);
    const digestBuffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(artifactText.value));
    const digest = Array.from(new Uint8Array(digestBuffer)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
    await workflowStore.registerRevision({
      workflow_id: snapshot.workflow_id,
      expected_attempt_id: snapshot.attempt.attempt_id,
      expected_sequence: snapshot.sequence,
      source_artifact_id: artifact.artifact_id,
      kind: artifact.kind as 'transcript_markdown' | 'final_summary_markdown',
      staged_path: stagedPath,
      size_bytes: new TextEncoder().encode(artifactText.value).byteLength,
      sha256: digest,
    });
    editingArtifactId.value = null;
    artifactText.value = '';
  } catch (reason) {
    artifactError.value = String(reason);
  } finally {
    artifactSaving.value = false;
  }
}

function statusLabel(snapshot: WorkflowSnapshot): string {
  if (snapshot.status === 'completed') return '已完成';
  if (snapshot.status === 'completed_with_warnings') return '已完成（有警告）';
  if (snapshot.status === 'failed') return '失败';
  if (snapshot.status === 'interrupted') return '已中断';
  if (snapshot.status === 'waiting_for_secret') return '等待凭据';
  if (snapshot.stage === 'transcribing') return '转录中';
  if (snapshot.stage === 'summarizing') return '总结中';
  if (snapshot.stage === 'writing_final') return '写入文件';
  return snapshot.status === 'queued' ? '排队中' : '准备中';
}

function ratio(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
}

function percent(value: number | null | undefined): string {
  const normalized = ratio(value);
  return normalized === null ? '等待进度数据' : `${Math.round(normalized * 100)}%`;
}

function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  const totalSeconds = Math.floor(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function progressSummary(snapshot: WorkflowSnapshot): string {
  if (snapshot.status === 'queued') {
    const position = snapshot.progress.queue_position;
    return typeof position === 'number' ? `排队第 ${position + 1} 位` : '等待调度';
  }
  return statusLabel(snapshot);
}

function updatedAt(snapshot: WorkflowSnapshot): string {
  const date = new Date(snapshot.timestamps.updated_at);
  return Number.isNaN(date.getTime()) ? snapshot.timestamps.updated_at : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function phaseLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    starting_transcription: '启动转录',
    gpu_waiting: '等待本地 GPU 通道',
    audio_normalizing: '解码与标准化音频',
    dependency_importing: '加载运行依赖',
    diarization_loading: '加载 Pyannote 说话人模型',
    diarizing: '分析说话人时间轴',
    segmenting: '生成安全转录分块',
    model_loading: '加载 ASR 模型权重',
    processor_loading: '加载音频处理器',
    model_moving_to_device: '迁移模型到推理设备',
    feature_extracting: '提取音频特征',
    generating: '语音识别与说话人分析',
    formatting_transcript: '整理转录结果',
    transcribing: '按分块执行语音识别',
    releasing_model: '释放上一阶段显存',
    cloud_asr_request: '调用云端语音识别',
    legacy_transcription: '兼容转录流程',
  };
  return value ? labels[value] || value : '等待阶段信息';
}
</script>

<template>
  <section class="view-grid workflow-grid">
    <div class="panel form-panel">
      <header class="panel-header">
        <div>
          <h1>一键工作流</h1>
          <p>上传录音 → {{ pipelineLabel }} 转录 → 选择模板总结 → 输出 Markdown。</p>
        </div>
        <button class="primary" type="button" :disabled="submitting || !workflowStore.runtime || !privacyConfirmed" @click="submit">
          <Play :size="17" />
          {{ submitting ? '提交中' : '开始一键处理' }}
        </button>
      </header>

      <div v-if="error" class="workflow-error">{{ error }}</div>
      <div v-if="workflowStore.runtimeStatus && ['unavailable', 'error'].includes(workflowStore.runtimeStatus.state)" class="workflow-error">
        Runtime 不可用：{{ workflowStore.runtimeStatus.detail || '请查看 AppData/ASR Local/logs 中的诊断日志' }}
      </div>

      <label>
        <span>音频文件</span>
        <div class="input-action">
          <input v-model="sourcePath" type="text" placeholder="选择或粘贴音频路径" />
          <button type="button" title="选择音频" @click="chooseAudio"><FolderOpen :size="17" /></button>
        </div>
      </label>

      <div class="two-col">
        <label>
          <span>输出目录</span>
          <div class="input-action">
            <input v-model="outputDir" type="text" />
            <button type="button" title="选择目录" @click="chooseOutputDir"><FolderOpen :size="17" /></button>
          </div>
        </label>
        <label>
          <span>输出文件名</span>
          <input v-model="baseName" type="text" />
        </label>
      </div>

      <div class="two-col">
        <label>
          <span>转录链路</span>
          <select v-model="pipelineProfile">
            <option value="pyannote_qwen3_asr">Pyannote + Qwen3-ASR（推荐）</option>
          </select>
          <small class="field-hint">先执行 Pyannote 说话人分析，再按安全分块使用 Qwen3-ASR 转录。</small>
        </label>
        <label>
          <span>推理设备</span>
          <select v-model="devicePolicy">
            <option value="auto">自动判断 CPU / GPU</option>
            <option value="cpu">强制 CPU</option>
            <option value="cuda">强制 CUDA（不可用时失败）</option>
          </select>
        </label>
        <label>
          <span>总结模型 Profile</span>
          <select v-model="selectedProfileName">
            <option value="">未选择</option>
            <option v-for="profile in availableProfiles" :key="profile.id" :value="profile.name">
              {{ profile.name }} · {{ profile.model }}
            </option>
          </select>
        </label>
      </div>

      <p class="field-hint">本地链路统一混音为单声道后执行 Pyannote 说话人分析。</p>

      <div v-if="selectedProfile" class="privacy-confirmation">
        <strong>云端总结授权</strong>
        <p>{{ providerAuthorizationText }}</p>
        <label class="checkbox-row">
          <input v-model="privacyConfirmed" type="checkbox" />
          <span>我确认已了解上述 provider、模型和文本出站范围，并授权本次任务使用。</span>
        </label>
      </div>

      <label>
        <span>总结模板</span>
        <select v-model="selectedTemplateName">
          <option value="">未选择</option>
          <option v-for="template in availableTemplates" :key="template.id" :value="template.name">
            {{ template.name }}
          </option>
        </select>
      </label>

      <label>
        <span>录音背景</span>
        <textarea v-model="recordingBackground" rows="3" placeholder="例如：研发周会，参与人包括产品和工程团队。" />
      </label>

      <div class="two-col">
        <label>
          <span>热词（每行一个）</span>
          <textarea v-model="hotwordsText" rows="4" placeholder="Qwen\nASR Local" />
        </label>
        <label>
          <span>额外转录指令</span>
          <textarea v-model="extraInstruction" rows="4" placeholder="保留专有名词，不要编造内容。" />
        </label>
      </div>
    </div>

    <aside class="side-stack">
      <div class="panel task-center">
        <header class="task-center-header">
          <div>
            <h2>任务中心</h2>
            <small>最多并行 {{ workflowStore.capabilities?.max_inflight_workflows ?? '—' }} 个任务</small>
          </div>
          <button class="ghost icon-button" type="button" title="刷新任务" :disabled="refreshing" @click="refresh">
            <RefreshCw :size="16" :class="{ spinning: refreshing }" />
          </button>
        </header>

        <div class="task-summary">
          <span><i class="status-dot running" />运行中 <strong>{{ runningCount }}</strong></span>
          <span><i class="status-dot queued" />排队中 <strong>{{ queuedCount }}</strong></span>
        </div>

        <ol class="task-list">
          <li
            v-for="snapshot in activeWorkflows"
            :key="snapshot.workflow_id"
            class="task-item"
            :class="{ selected: selectedWorkflow?.workflow_id === snapshot.workflow_id }"
          >
            <button class="task-row" type="button" @click="workflowStore.select(snapshot.workflow_id)">
              <span class="task-identity">
                <strong>{{ snapshot.spec.display_name }}</strong>
                <small>{{ progressSummary(snapshot) }} · {{ updatedAt(snapshot) }}</small>
              </span>
              <span class="task-row-progress">
                <span>{{ percent(snapshot.progress.overall_ratio) }}</span>
                <ChevronDown v-if="selectedWorkflow?.workflow_id === snapshot.workflow_id" :size="16" />
                <ChevronRight v-else :size="16" />
              </span>
            </button>
            <div class="progress-track compact"><span :style="{ width: `${(ratio(snapshot.progress.overall_ratio) ?? 0) * 100}%` }" /></div>

            <div v-if="selectedWorkflow?.workflow_id === snapshot.workflow_id" class="task-expanded">
              <div class="progress-hero">
                <strong>{{ percent(snapshot.progress.overall_ratio) }}</strong>
                <div class="progress-track"><span :style="{ width: `${(ratio(snapshot.progress.overall_ratio) ?? 0) * 100}%` }" /></div>
              </div>
              <dl class="progress-details">
                <div><dt>当前阶段</dt><dd>{{ statusLabel(snapshot) }} · {{ percent(snapshot.progress.stage_ratio) }}</dd></div>
                <div v-if="snapshot.status === 'queued'"><dt>队列位置</dt><dd>{{ progressSummary(snapshot) }}</dd></div>
                <div><dt>音频进度</dt><dd>{{ formatDuration(snapshot.progress.processed_ms) }} / {{ formatDuration(snapshot.progress.total_ms) }}</dd></div>
                <div><dt>当前步骤</dt><dd>{{ snapshot.progress.detail || '等待工作进程上报详细信息' }}</dd></div>
                <div v-if="snapshot.progress.phase"><dt>运行子阶段</dt><dd>{{ phaseLabel(snapshot.progress.phase) }}</dd></div>
                <div v-if="snapshot.progress.heartbeat_at"><dt>最后心跳</dt><dd><Clock3 :size="14" />{{ updatedAt({ ...snapshot, timestamps: { ...snapshot.timestamps, updated_at: snapshot.progress.heartbeat_at } }) }}</dd></div>
                <div><dt>更新时间</dt><dd><Clock3 :size="14" />{{ updatedAt(snapshot) }}</dd></div>
              </dl>
              <p v-if="snapshot.last_error" class="workflow-error">{{ snapshot.last_error.message }}</p>
              <div class="workflow-actions">
                <button v-if="canPause(snapshot)" type="button" :disabled="Boolean(snapshot.control.pending_action)" @click="control('pause')"><Pause :size="15" />{{ snapshot.control.pending_action === 'pause' ? '暂停中' : '暂停' }}</button>
                <button v-if="canResume(snapshot)" type="button" :disabled="Boolean(snapshot.control.pending_action)" @click="control('resume')"><Play :size="15" />继续</button>
                <button v-if="canCancel(snapshot)" class="danger" type="button" :disabled="Boolean(snapshot.control.pending_action)" @click="control('cancel')"><Square :size="15" />{{ snapshot.control.pending_action === 'cancel' ? '取消中' : '取消' }}</button>
              </div>
            </div>
          </li>
          <li v-if="activeWorkflows.length === 0" class="empty-state">当前没有运行或排队任务。</li>
        </ol>

        <section v-if="recentWorkflows.length" class="recent-task-section">
          <button class="section-toggle" type="button" @click="recentExpanded = !recentExpanded">
            <span>最近任务 <strong>{{ recentWorkflows.length }}</strong></span>
            <ChevronDown v-if="recentExpanded" :size="16" />
            <ChevronRight v-else :size="16" />
          </button>
          <ol v-if="recentExpanded" class="task-list recent">
            <li v-for="snapshot in recentWorkflows.slice(0, 6)" :key="snapshot.workflow_id" class="task-item terminal" :class="{ selected: selectedWorkflow?.workflow_id === snapshot.workflow_id }">
              <button class="task-row" type="button" @click="workflowStore.select(snapshot.workflow_id)">
                <span class="task-identity"><strong>{{ snapshot.spec.display_name }}</strong><small>{{ updatedAt(snapshot) }}</small></span>
                <span class="status-badge" :data-status="snapshot.status">{{ statusLabel(snapshot) }}</span>
              </button>
              <div v-if="selectedWorkflow?.workflow_id === snapshot.workflow_id" class="task-expanded terminal-detail">
                <div class="terminal-actions">
                  <button v-if="['failed', 'completed', 'interrupted'].includes(snapshot.status)" type="button" @click="retry"><RotateCcw :size="15" />重试</button>
                  <button class="clear-task" type="button" :disabled="clearingWorkflowId === snapshot.workflow_id" @click="clearWorkflow(snapshot)"><Trash2 :size="15" />{{ clearingWorkflowId === snapshot.workflow_id ? '清除中' : '清除记录' }}</button>
                </div>
                <button class="diagnostics-toggle" type="button" @click="diagnosticsExpanded = !diagnosticsExpanded">
                  <span>产物与诊断信息</span><ChevronDown v-if="diagnosticsExpanded" :size="15" /><ChevronRight v-else :size="15" />
                </button>
                <div v-if="diagnosticsExpanded" class="diagnostics-content">
                  <div v-if="snapshot.artifacts.length" class="artifact-list">
                    <div v-for="artifact in snapshot.artifacts" :key="artifact.artifact_id">
                      <strong>{{ artifact.kind }}</strong><span>{{ artifact.path }}{{ artifact.stale ? ' · 已过期' : '' }}</span><button type="button" @click="editArtifact(artifact.artifact_id)">编辑</button>
                    </div>
                  </div>
                  <ol v-if="snapshot.timeline?.length" class="workflow-timeline">
                    <li v-for="entry in snapshot.timeline.slice(-8).reverse()" :key="`${entry.sequence}-${entry.type}`"><strong>#{{ entry.sequence }} · {{ entry.type }}</strong><span>{{ entry.stage || '—' }} · {{ entry.occurred_at }}</span></li>
                  </ol>
                </div>
                <div v-if="editingArtifactId" class="artifact-editor">
                  <textarea v-model="artifactText" rows="10" />
                  <p v-if="artifactError" class="workflow-error">{{ artifactError }}</p>
                  <div class="workflow-actions"><button type="button" :disabled="artifactSaving" @click="saveArtifactRevision">{{ artifactSaving ? '保存中' : '保存为新版本' }}</button><button class="ghost" type="button" @click="editingArtifactId = null">取消</button></div>
                </div>
              </div>
            </li>
          </ol>
        </section>
      </div>
    </aside>
  </section>
</template>
