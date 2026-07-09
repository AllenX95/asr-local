<script setup lang="ts">
import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';
import { Download, FolderOpen, Save } from '@lucide/vue';
import { computed, ref, watch } from 'vue';
import LatestWorkerResultSelect from '../../components/LatestWorkerResultSelect.vue';
import MarkdownEditor from './MarkdownEditor.vue';
import { useAppStore } from '../../stores/appStore';

const store = useAppStore();
const renderer = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true
});

const previewSource = ref(store.markdown.content);
let previewTimer: number | undefined;

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
      <LatestWorkerResultSelect />
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
