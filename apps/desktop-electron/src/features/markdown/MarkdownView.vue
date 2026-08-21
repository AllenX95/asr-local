<script setup lang="ts">
import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';
import { Download, FolderOpen, Save, Sparkles } from '@lucide/vue';
import { computed, ref, watch } from 'vue';
import MarkdownEditor from './MarkdownEditor.vue';
import { api } from '../../ipc/desktopClient';
import type { WorkflowCatalogs, WorkflowSummaryProfile, WorkflowSummaryTemplate } from '../../ipc/workerTypes';
import { useAppStore } from '../../stores/appStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import type { WorkflowSnapshot } from '../../workflows/types';

type MarkdownMode = 'transcript' | 'summary';

const props = withDefaults(defineProps<{ mode?: MarkdownMode }>(), { mode: 'summary' });
const store = useAppStore();
const workflowStore = useWorkflowStore();
const renderer = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true
});

const previewSource = ref(store.markdown.content);
const selectedWorkflowId = ref('');
const artifactBindingPath = ref('');
const catalogs = ref<WorkflowCatalogs>({ summary_profiles: [], summary_templates: [] });
const summaryProfileName = ref('');
const summaryTemplateName = ref('');
const summaryPrivacyConfirmed = ref(false);
const summaryGenerating = ref(false);
const summaryError = ref('');
let previewTimer: number | undefined;

const mode = computed(() => props.mode);
const isTranscriptMode = computed(() => props.mode === 'transcript');
const artifactKind = computed(() => isTranscriptMode.value ? 'transcript_markdown' : 'final_summary_markdown');
const artifactTitle = computed(() => isTranscriptMode.value ? '转录 Markdown' : '总结 Markdown');

const artifactOptions = computed(() => workflowStore.workflows
  .map((workflow) => ({ workflow, artifact: latestArtifact(workflow, artifactKind.value) }))
  .filter((item): item is { workflow: WorkflowSnapshot; artifact: WorkflowSnapshot['artifacts'][number] } => Boolean(item.artifact))
  .map(({ workflow, artifact }) => ({
    id: workflow.workflow_id,
    label: `${workflow.spec.display_name} · ${new Date(workflow.timestamps.completed_at || workflow.timestamps.updated_at).toLocaleString('zh-CN')}`,
    path: artifact.path,
    workflow,
    artifact,
  })));

function latestArtifact(workflow: WorkflowSnapshot, kind: string) {
  return workflow.artifacts
    .filter((artifact) => artifact.kind === kind && !artifact.stale)
    .sort((left, right) => right.revision - left.revision || right.created_at.localeCompare(left.created_at))[0];
}

const selectedArtifact = computed(() => artifactOptions.value.find((option) => option.id === selectedWorkflowId.value) ?? null);
const availableProfiles = computed<WorkflowSummaryProfile[]>(() => {
  if (catalogs.value.summary_profiles.length) return catalogs.value.summary_profiles;
  return store.summaryProfiles.profiles.map((profile) => ({
    id: profile.id || `summary-profile-${profile.name}`,
    version: profile.version || 1,
    name: profile.name,
    base_url: profile.base_url,
    model: profile.model,
    max_input_tokens: profile.max_input_tokens,
    max_output_tokens: profile.max_output_tokens,
    auth_mode: profile.api_key.trim() ? 'bearer' : 'none',
    provider_binding_sha256: `catalog:${profile.id}:v${profile.version}`,
  }));
});
const availableTemplates = computed<WorkflowSummaryTemplate[]>(() => {
  if (catalogs.value.summary_templates.length) return catalogs.value.summary_templates;
  return store.summaryTemplates.map((template) => ({
    id: template.id || `summary-template-${template.name}`,
    version: template.version || 1,
    name: template.name,
    prompt: template.prompt,
  }));
});
const selectedProfile = computed(() => availableProfiles.value.find((profile) => profile.name === summaryProfileName.value) ?? null);
const selectedTemplate = computed(() => availableTemplates.value.find((template) => template.name === summaryTemplateName.value) ?? null);
const providerAuthorizationText = computed(() => selectedProfile.value
  ? `总结文本将发送到 ${selectedProfile.value.base_url}，使用模型 ${selectedProfile.value.model}。`
  : '');

watch(artifactOptions, (options) => {
  const selected = options.find((option) => option.id === selectedWorkflowId.value);
  if (!selected) {
    selectedWorkflowId.value = '';
    artifactBindingPath.value = '';
    return;
  }
  if (selected.path !== artifactBindingPath.value && store.markdown.path === artifactBindingPath.value && !store.markdownDirty) {
    void openSelectedArtifact();
  }
}, { deep: true });

watch(mode, (nextMode, previousMode) => {
  if (nextMode === previousMode) return;
  if (!selectedArtifact.value) {
    selectedWorkflowId.value = '';
    artifactBindingPath.value = '';
    return;
  }
  void openSelectedArtifact();
});

watch(() => store.markdown.path, (currentPath) => {
  if (artifactBindingPath.value && currentPath !== artifactBindingPath.value) {
    artifactBindingPath.value = '';
    selectedWorkflowId.value = '';
  }
});

watch(() => store.initialized, (initialized) => {
  if (!initialized) return;
  void api.loadWorkflowCatalogs().then((value) => {
    catalogs.value = value;
    if (!summaryProfileName.value) summaryProfileName.value = value.summary_profiles[0]?.name || store.summaryProfiles.profiles[0]?.name || '';
    if (!summaryTemplateName.value) summaryTemplateName.value = value.summary_templates[0]?.name || store.summaryTemplates[0]?.name || '';
  }).catch((reason) => { summaryError.value = String(reason); });
  if (!summaryProfileName.value) summaryProfileName.value = store.summaryProfiles.profiles[0]?.name || '';
  if (!summaryTemplateName.value) summaryTemplateName.value = store.summaryTemplates[0]?.name || '';
}, { immediate: true });

watch(summaryProfileName, () => { summaryPrivacyConfirmed.value = false; });

async function openSelectedArtifact(): Promise<void> {
  const option = selectedArtifact.value;
  if (!option) return;
  await store.loadMarkdownPath(option.path, true, mode.value);
  if (store.markdown.path === option.path) artifactBindingPath.value = option.path;
}

async function openExternalFile(): Promise<void> {
  selectedWorkflowId.value = '';
  artifactBindingPath.value = '';
  await store.openMarkdownFile(mode.value);
  selectedWorkflowId.value = '';
  artifactBindingPath.value = '';
}

const canGenerateSummary = computed(() => Boolean(
  isTranscriptMode.value
  && selectedArtifact.value
  && store.markdown.path === selectedArtifact.value.path
  && !store.markdownDirty,
));
const generationHint = computed(() => {
  if (!selectedArtifact.value) return '请先选择包含 transcript 的任务。';
  if (store.markdown.path !== selectedArtifact.value.path) return '请先打开所选任务的最新 transcript；当前文件不是该产物。';
  if (store.markdownDirty) return '当前 transcript 有未保存编辑；请保存为产物版本后再生成总结。';
  return '当前 transcript 产物已绑定，可以生成总结。';
});

async function generateSummary(): Promise<void> {
  const option = selectedArtifact.value;
  if (!option || !selectedProfile.value || !selectedTemplate.value) {
    summaryError.value = '请选择转录稿、总结模型和总结模板。';
    return;
  }
  if (!canGenerateSummary.value) {
    summaryError.value = generationHint.value;
    return;
  }
  if (!summaryPrivacyConfirmed.value) {
    summaryError.value = '请确认转录文本将发送到所选总结服务后再开始生成。';
    return;
  }
  summaryGenerating.value = true;
  summaryError.value = '';
  try {
    await workflowStore.resummarize({
      source_workflow_id: option.workflow.workflow_id,
      expected_attempt_id: option.workflow.attempt.attempt_id,
      expected_sequence: option.workflow.sequence,
      input_artifact_id: option.artifact.artifact_id,
      summary: {
        profile_id: selectedProfile.value.id,
        profile_version: selectedProfile.value.version,
        template: { id: selectedTemplate.value.id, version: selectedTemplate.value.version },
      },
    });
    store.setStatus('总结任务已提交', `${option.workflow.spec.display_name} · ${selectedProfile.value.model}`);
  } catch (reason) {
    summaryError.value = String(reason);
  } finally {
    summaryGenerating.value = false;
  }
}

watch(
  () => store.markdown.content,
  (value) => {
    window.clearTimeout(previewTimer);
    const delay = value.length > 2 * 1024 * 1024 ? 900 : 220;
    previewTimer = window.setTimeout(() => {
      previewSource.value = value;
    }, delay);
  }
);

const renderedHtml = computed(() =>
  DOMPurify.sanitize(renderer.render(previewSource.value), {
    USE_PROFILES: { html: true }
  })
);
</script>

<template>
  <section class="view-column markdown-view" :class="{ 'transcript-mode': isTranscriptMode }">
    <header class="view-header">
      <div>
        <h1>{{ artifactTitle }}</h1>
        <p>{{ isTranscriptMode ? '查看转录稿，并从当前 transcript 旁路生成总结。' : '查看最终总结稿、编辑 Markdown 并保存本地版本。' }}</p>
      </div>
      <div class="toolbar">
        <select v-model="selectedWorkflowId" :disabled="!artifactOptions.length" :title="`打开${artifactTitle}`" @change="openSelectedArtifact">
          <option value="">选择要打开的{{ artifactTitle }}…</option>
          <option v-for="option in artifactOptions" :key="option.id" :value="option.id">
            {{ option.label }}
          </option>
        </select>
        <button type="button" @click="openExternalFile">
          <FolderOpen :size="17" />
          打开
        </button>
        <button class="primary" type="button" @click="store.saveMarkdown">
          <Save :size="17" />
          保存
        </button>
        <button type="button" :disabled="!store.markdown.path" @click="store.openPath(store.markdown.path)">
          <Download :size="17" />
          定位
        </button>
      </div>
    </header>

    <div v-if="isTranscriptMode" class="panel transcript-summary-panel">
      <header class="panel-header compact">
        <div>
          <h2>从当前转录生成总结</h2>
          <p>复用已有 transcript，不重新执行语音识别；生成任务会出现在任务中心。</p>
        </div>
        <Sparkles :size="18" />
      </header>
      <div class="two-col">
        <label>
          <span>总结模型</span>
          <select v-model="summaryProfileName">
            <option value="">未选择</option>
            <option v-for="profile in availableProfiles" :key="profile.id" :value="profile.name">
              {{ profile.name }} · {{ profile.model }}
            </option>
          </select>
        </label>
        <label>
          <span>总结模板</span>
          <select v-model="summaryTemplateName">
            <option value="">未选择</option>
            <option v-for="template in availableTemplates" :key="template.id" :value="template.name">
              {{ template.name }}
            </option>
          </select>
        </label>
      </div>
      <div v-if="selectedProfile" class="privacy-confirmation">
        <strong>云端总结授权</strong>
        <p>{{ providerAuthorizationText }}</p>
        <label class="checkbox-row">
          <input v-model="summaryPrivacyConfirmed" type="checkbox" />
          <span>我确认已了解上述 provider、模型和转录文本出站范围，并授权本次总结。</span>
        </label>
      </div>
      <p v-if="summaryError" class="workflow-error">{{ summaryError }}</p>
      <div class="workflow-actions">
        <button class="primary" type="button" :disabled="summaryGenerating || !canGenerateSummary || !selectedProfile || !selectedTemplate || !summaryPrivacyConfirmed" @click="generateSummary">
          <Sparkles :size="15" />
          {{ summaryGenerating ? '提交中' : '生成总结' }}
        </button>
        <small v-if="selectedArtifact">来源：{{ selectedArtifact.workflow.spec.display_name }} · {{ generationHint }}</small>
        <small v-else>{{ generationHint }}</small>
      </div>
    </div>

    <div class="panel path-panel">
      <label>
        <span>当前文件</span>
        <input v-model="store.markdown.path" type="text" />
      </label>
      <span class="dirty-badge" :class="{ active: store.markdownDirty }">
        {{ store.markdownDirty ? '未保存' : '已保存' }}
      </span>
      <span v-if="store.markdown.largeMode" class="large-badge">性能模式</span>
    </div>

    <div class="markdown-split">
      <div class="editor-pane">
        <MarkdownEditor v-model="store.markdown.content" :large-mode="store.markdown.largeMode" />
      </div>
      <article class="preview-pane markdown-body" v-html="renderedHtml" />
    </div>
  </section>
</template>
