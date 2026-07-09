<script setup lang="ts">
import { FileAudio2, FileText, History, Settings, Sparkles } from '@lucide/vue';
import { useAppStore } from '../stores/appStore';
import type { ViewKey } from '../ipc/workerTypes';

const store = useAppStore();

const items: Array<{ key: ViewKey; label: string; icon: typeof FileAudio2 }> = [
  { key: 'workbench', label: '工作台', icon: FileAudio2 },
  { key: 'markdown', label: 'Markdown', icon: FileText },
  { key: 'summary', label: '总结', icon: Sparkles },
  { key: 'history', label: '历史', icon: History },
  { key: 'settings', label: '设置', icon: Settings }
];
</script>

<template>
  <aside class="nav-rail">
    <div class="brand-block">
      <div class="brand-mark">ASR</div>
      <div>
        <strong>听记助手</strong>
        <span>Tauri</span>
      </div>
    </div>
    <nav>
      <button
        v-for="item in items"
        :key="item.key"
        class="nav-button"
        :class="{ active: store.activeView === item.key }"
        type="button"
        @click="store.setActiveView(item.key)"
      >
        <component :is="item.icon" :size="18" />
        <span>{{ item.label }}</span>
      </button>
    </nav>
  </aside>
</template>
