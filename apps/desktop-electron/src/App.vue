<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted } from 'vue';
import NavRail from './components/NavRail.vue';
import { useAppStore } from './stores/appStore';
import { useWorkflowStore } from './stores/workflowStore';
import { ElectronWorkflowRuntime } from './workflows/adapters/electronWorkflowRuntime';

const store = useAppStore();
const workflowStore = useWorkflowStore();
const WorkflowView = defineAsyncComponent(() => import('./features/workflow/WorkflowView.vue'));
const MarkdownView = defineAsyncComponent(() => import('./features/markdown/MarkdownView.vue'));
const HistoryView = defineAsyncComponent(() => import('./features/history/HistoryView.vue'));
const SettingsView = defineAsyncComponent(() => import('./features/settings/SettingsView.vue'));

const currentView = computed(() => {
  switch (store.activeView) {
    case 'workflow':
      return WorkflowView;
    case 'markdown':
      return MarkdownView;
    case 'history':
      return HistoryView;
    case 'settings':
      return SettingsView;
    default:
      return WorkflowView;
  }
});

onMounted(() => {
  void workflowStore.configure(new ElectronWorkflowRuntime()).catch((error) => {
    store.setError('Workflow Runtime 启动失败', error);
  });
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
