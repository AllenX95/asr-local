<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { FolderOpen, Pause, Play, RefreshCw, RotateCcw, Square } from '@lucide/vue';
import { api } from '../../ipc/tauriClient';
import type { WorkflowCatalogs, WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../../ipc/workerTypes';
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
const channelStrategy = ref<'mixdown' | 'split_stereo'>('mixdown');
const pipelineProfile = ref<'moss_transcribe_diarize' | 'qwen3_asr_with_pyannote'>('moss_transcribe_diarize');
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
const pipelineLabel = computed(() => pipelineProfile.value === 'moss_transcribe_diarize' ? 'MOSS' : 'Legacy');

watch(selectedProfileName, () => {
  privacyConfirmed.value = false;
});
const selectedWorkflow = computed<WorkflowSnapshot | null>(() => {
  const id = workflowStore.selectedWorkflowId;
  return id ? workflowStore.workflowsById[id] ?? null : workflowStore.workflows[0] ?? null;
});

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
        channel_strategy: pipelineProfile.value === 'moss_transcribe_diarize'
          ? channelStrategy.value
          : 'mixdown',
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
  try {
    await workflowStore.refresh();
  } catch (reason) {
    error.value = String(reason);
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
  if (snapshot.status === 'failed') return '失败';
  if (snapshot.status === 'interrupted') return '已中断';
  if (snapshot.status === 'waiting_for_secret') return '等待凭据';
  if (snapshot.stage === 'transcribing') return '转录中';
  if (snapshot.stage === 'summarizing') return '总结中';
  if (snapshot.stage === 'writing_final') return '写入文件';
  return snapshot.status === 'queued' ? '排队中' : '准备中';
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
            <option value="moss_transcribe_diarize">MOSS-Diarize（默认）</option>
            <option value="qwen3_asr_with_pyannote">Legacy Qwen + pyannote（显式回退）</option>
          </select>
          <small v-if="pipelineProfile === 'qwen3_asr_with_pyannote'" class="field-hint">需在设置中配置 Qwen3-ASR 与 pyannote；不会改变 MOSS 默认。</small>
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

      <label v-if="pipelineProfile === 'moss_transcribe_diarize'">
        <span>音频声道</span>
        <select v-model="channelStrategy">
          <option value="mixdown">自动混音为单声道（推荐）</option>
          <option value="split_stereo">左右声道分别转写</option>
        </select>
        <small class="field-hint">仅适用于左右声道分别录制不同发言人的双通道录音。</small>
      </label>

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
          <textarea v-model="hotwordsText" rows="4" placeholder="MOSS\nASR Local" />
        </label>
        <label>
          <span>额外转录指令</span>
          <textarea v-model="extraInstruction" rows="4" placeholder="保留专有名词，不要编造内容。" />
        </label>
      </div>
    </div>

    <aside class="side-stack">
      <div class="panel">
        <header class="panel-header compact">
          <div>
            <h2>任务队列</h2>
            <small>最多同时运行 {{ 3 }} 个工作流，超出任务自动排队。</small>
          </div>
          <button class="ghost" type="button" title="刷新" @click="refresh"><RefreshCw :size="16" /></button>
        </header>
        <ol class="workflow-list">
          <li
            v-for="snapshot in workflowStore.workflows"
            :key="snapshot.workflow_id"
            :class="{ selected: selectedWorkflow?.workflow_id === snapshot.workflow_id }"
            @click="workflowStore.select(snapshot.workflow_id)"
          >
            <div>
              <strong>{{ snapshot.spec.display_name }}</strong>
              <span>{{ statusLabel(snapshot) }}</span>
            </div>
            <small>#{{ snapshot.attempt.number }} · seq {{ snapshot.sequence }}</small>
          </li>
          <li v-if="workflowStore.workflows.length === 0" class="empty-state">还没有提交任务。</li>
        </ol>
      </div>

      <div v-if="selectedWorkflow" class="panel workflow-detail">
        <header class="panel-header compact">
          <div>
            <h2>{{ selectedWorkflow.spec.display_name }}</h2>
            <small>{{ statusLabel(selectedWorkflow) }} · {{ selectedWorkflow.stage || '等待' }}</small>
          </div>
          <button class="ghost" type="button" title="刷新任务" @click="refresh"><RotateCcw :size="16" /></button>
        </header>
        <div class="progress-track"><span :style="{ width: `${Math.round(Number(selectedWorkflow.progress.overall_ratio || 0) * 100)}%` }" /></div>
        <p v-if="selectedWorkflow.last_error" class="workflow-error">{{ selectedWorkflow.last_error.message }}</p>
        <div class="workflow-actions">
          <button v-if="canPause(selectedWorkflow)" type="button" @click="control('pause')"><Pause :size="15" />暂停</button>
          <button v-if="canResume(selectedWorkflow)" type="button" @click="control('resume')"><Play :size="15" />继续</button>
          <button v-if="canCancel(selectedWorkflow)" class="danger" type="button" @click="control('cancel')"><Square :size="15" />取消</button>
          <button v-if="['failed', 'completed', 'interrupted'].includes(selectedWorkflow.status)" type="button" @click="retry"><RotateCcw :size="15" />重试</button>
        </div>
        <div v-if="selectedWorkflow.artifacts.length" class="artifact-list">
          <div v-for="artifact in selectedWorkflow.artifacts" :key="artifact.artifact_id">
            <strong>{{ artifact.kind }}</strong>
            <span>{{ artifact.path }}{{ artifact.stale ? ' · 已过期' : '' }}</span>
            <button type="button" @click="editArtifact(artifact.artifact_id)">编辑</button>
          </div>
        </div>
        <ol v-if="selectedWorkflow.timeline?.length" class="workflow-timeline">
          <li v-for="entry in selectedWorkflow.timeline.slice(-8).reverse()" :key="`${entry.sequence}-${entry.type}`">
            <strong>#{{ entry.sequence }} · {{ entry.type }}</strong>
            <span>{{ entry.stage || '—' }} · {{ entry.occurred_at }}</span>
          </li>
        </ol>
        <div v-if="editingArtifactId" class="artifact-editor">
          <textarea v-model="artifactText" rows="10" />
          <p v-if="artifactError" class="workflow-error">{{ artifactError }}</p>
          <div class="workflow-actions">
            <button type="button" :disabled="artifactSaving" @click="saveArtifactRevision">{{ artifactSaving ? '保存中' : '保存为新版本' }}</button>
            <button class="ghost" type="button" @click="editingArtifactId = null">取消</button>
          </div>
        </div>
      </div>
    </aside>
  </section>
</template>
