<script setup lang="ts">
import { ExternalLink, FileText, RefreshCw } from '@lucide/vue';
import { useAppStore } from '../../stores/appStore';
import { useWorkflowStore } from '../../stores/workflowStore';

const store = useAppStore();
const workflowStore = useWorkflowStore();

function formatTime(value: number) {
  if (!value) {
    return '';
  }
  return new Date(value).toLocaleString();
}

function formatSize(value: number) {
  if (value >= 1024 * 1024) {
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

async function refreshAll(): Promise<void> {
  await store.refreshHistory();
  if (workflowStore.runtime) await workflowStore.refresh();
}
</script>

<template>
  <section class="view-column">
    <header class="view-header">
      <div>
        <h1>历史</h1>
        <p>扫描 outputs 目录中的转写稿、总结稿和最终稿。</p>
      </div>
      <button type="button" @click="refreshAll">
        <RefreshCw :size="17" />
        刷新
      </button>
    </header>

    <div class="panel table-panel">
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>文件</th>
            <th>大小</th>
            <th>修改时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in store.history" :key="item.id">
            <td><span class="kind-pill">{{ item.kind }}</span></td>
            <td>
              <button class="link-button" type="button" @click="store.loadMarkdownPath(item.path)">
                <FileText :size="16" />
                {{ item.title }}
              </button>
              <small>{{ item.path }}</small>
            </td>
            <td>{{ formatSize(item.size_bytes) }}</td>
            <td>{{ formatTime(item.modified_ms) }}</td>
            <td>
              <button class="icon-button" type="button" title="定位文件" @click="store.openPath(item.path)">
                <ExternalLink :size="16" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="workflowStore.workflows.length" class="panel table-panel">
      <header class="panel-header compact">
        <div>
          <h2>Workflow v2 任务</h2>
          <small>registry 快照与最终产物，不与旧版 lane 历史混合。</small>
        </div>
      </header>
      <table>
        <thead>
          <tr><th>任务</th><th>状态</th><th>尝试</th><th>产物</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="workflow in workflowStore.workflows" :key="workflow.workflow_id">
            <td><strong>{{ workflow.spec.display_name }}</strong><small>{{ workflow.workflow_id }}</small></td>
            <td><span class="kind-pill">{{ workflow.status }} · {{ workflow.stage || '—' }}</span></td>
            <td>#{{ workflow.attempt.number }}</td>
            <td>{{ workflow.artifacts.length }} 个</td>
            <td><button class="link-button" type="button" @click="workflowStore.select(workflow.workflow_id); store.setActiveView('workflow')">查看</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
