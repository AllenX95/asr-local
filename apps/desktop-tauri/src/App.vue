<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted } from 'vue';
import NavRail from './components/NavRail.vue';
import { useAppStore } from './stores/appStore';
import { useWorkflowStore } from './stores/workflowStore';
import { FakeWorkflowRuntime } from './workflows/adapters/fakeWorkflowRuntime';
import { ElectronWorkflowRuntime } from './workflows/adapters/electronWorkflowRuntime';
import { TauriWorkflowRuntime } from './workflows/adapters/tauriWorkflowRuntime';

const store = useAppStore();
const workflowStore = useWorkflowStore();
const WorkbenchView = defineAsyncComponent(() => import('./features/workbench/WorkbenchView.vue'));
const WorkflowView = defineAsyncComponent(() => import('./features/workflow/WorkflowView.vue'));
const MarkdownView = defineAsyncComponent(() => import('./features/markdown/MarkdownView.vue'));
const SummaryView = defineAsyncComponent(() => import('./features/summary/SummaryView.vue'));
const HistoryView = defineAsyncComponent(() => import('./features/history/HistoryView.vue'));
const SettingsView = defineAsyncComponent(() => import('./features/settings/SettingsView.vue'));

const currentView = computed(() => {
  switch (store.activeView) {
    case 'workflow':
      return WorkflowView;
    case 'markdown':
      return MarkdownView;
    case 'summary':
      return SummaryView;
    case 'history':
      return HistoryView;
    case 'settings':
      return SettingsView;
    default:
      return WorkbenchView;
  }
});

onMounted(() => {
  const isTauri = typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
  const runtime = window.asrLocal
    ? new ElectronWorkflowRuntime()
    : isTauri
      ? new TauriWorkflowRuntime()
      : new FakeWorkflowRuntime();
  void workflowStore.configure(runtime);
  void store.initialize();
});
</script>

<template>
  <div class="app-shell">
    <NavRail />
    <main class="main-surface">
      <component :is="currentView" />
    </main>
    <footer class="status-bar" :class="{ danger: Boolean(store.error) }">
      <strong>{{ store.statusTitle }}</strong>
      <span>{{ store.statusDetail }}</span>
    </footer>
  </div>
</template>
