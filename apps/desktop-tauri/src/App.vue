<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted } from 'vue';
import NavRail from './components/NavRail.vue';
import { useAppStore } from './stores/appStore';

const store = useAppStore();
const WorkbenchView = defineAsyncComponent(() => import('./features/workbench/WorkbenchView.vue'));
const MarkdownView = defineAsyncComponent(() => import('./features/markdown/MarkdownView.vue'));
const SummaryView = defineAsyncComponent(() => import('./features/summary/SummaryView.vue'));
const HistoryView = defineAsyncComponent(() => import('./features/history/HistoryView.vue'));
const SettingsView = defineAsyncComponent(() => import('./features/settings/SettingsView.vue'));

const currentView = computed(() => {
  switch (store.activeView) {
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
