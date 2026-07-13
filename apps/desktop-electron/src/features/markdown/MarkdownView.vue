<script setup lang="ts">
import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';
import { Download, FolderOpen, Save } from '@lucide/vue';
import { computed, ref, watch } from 'vue';
import MarkdownEditor from './MarkdownEditor.vue';
import { useAppStore } from '../../stores/appStore';
import { useWorkflowStore } from '../../stores/workflowStore';
import type { WorkflowSnapshot } from '../../workflows/types';

const store = useAppStore();
const workflowStore = useWorkflowStore();
const renderer = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true
});

const previewSource = ref(store.markdown.content);
const selectedCompletedWorkflowId = ref('');
let previewTimer: number | undefined;

const completedSummaryOptions = computed(() => workflowStore.workflows
  .filter((workflow) => workflow.status === 'completed' || workflow.status === 'completed_with_warnings')
  .map((workflow) => ({ workflow, artifact: latestSummaryArtifact(workflow) }))
  .filter((item): item is { workflow: WorkflowSnapshot; artifact: WorkflowSnapshot['artifacts'][number] } => Boolean(item.artifact))
  .map(({ workflow, artifact }) => ({
    id: workflow.workflow_id,
    label: `${workflow.spec.display_name} · ${new Date(workflow.timestamps.completed_at || workflow.timestamps.updated_at).toLocaleString('zh-CN')}`,
    path: artifact.path,
  })));

function latestSummaryArtifact(workflow: WorkflowSnapshot) {
  return workflow.artifacts
    .filter((artifact) => artifact.kind === 'final_summary_markdown' && !artifact.stale)
    .sort((left, right) => right.revision - left.revision || right.created_at.localeCompare(left.created_at))[0];
}

watch(completedSummaryOptions, (options) => {
  if (selectedCompletedWorkflowId.value && !options.some((option) => option.id === selectedCompletedWorkflowId.value)) selectedCompletedWorkflowId.value = '';
}, { deep: true });

async function openCompletedSummary(): Promise<void> {
  const option = completedSummaryOptions.value.find((item) => item.id === selectedCompletedWorkflowId.value);
  if (option) await store.loadMarkdownPath(option.path);
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
  <section class="view-column markdown-view">
    <header class="view-header">
      <div>
        <h1>Markdown</h1>
        <p>源码编辑、节流预览和本地保存。</p>
      </div>
      <div class="toolbar">
        <select v-model="selectedCompletedWorkflowId" :disabled="!completedSummaryOptions.length" title="打开已完成任务的总结" @change="openCompletedSummary">
          <option value="">打开已完成任务总结…</option>
          <option v-for="option in completedSummaryOptions" :key="option.id" :value="option.id">
            {{ option.label }}
          </option>
        </select>
        <button type="button" @click="store.openMarkdownFile">
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
