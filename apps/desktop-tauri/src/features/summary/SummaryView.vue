<script setup lang="ts">
import DOMPurify from 'dompurify';
import MarkdownIt from 'markdown-it';
import { Save, Sparkles } from '@lucide/vue';
import { computed } from 'vue';
import LatestWorkerResultSelect from '../../components/LatestWorkerResultSelect.vue';
import { useAppStore } from '../../stores/appStore';

const store = useAppStore();
const renderer = new MarkdownIt({ html: false, linkify: true, typographer: true });
const previewHtml = computed(() =>
  DOMPurify.sanitize(renderer.render(store.summary.output), {
    USE_PROFILES: { html: true }
  })
);
</script>

<template>
  <section class="summary-view">
    <div class="panel summary-config">
      <header class="panel-header">
        <div>
          <h1>总结</h1>
          <p>基于当前 Markdown 调用 OpenAI 兼容 API。</p>
        </div>
      </header>

      <div class="summary-config-row">
        <label>
          <span>Profile</span>
          <select v-model="store.summary.selectedProfileName">
            <option v-for="profile in store.summaryProfiles.profiles" :key="profile.name" :value="profile.name">
              {{ profile.name }} · {{ profile.model }}
            </option>
          </select>
        </label>
        <label>
          <span>模板</span>
          <select :value="store.summary.selectedTemplateName" @change="store.applyTemplate(($event.target as HTMLSelectElement).value)">
            <option v-for="template in store.summaryTemplates" :key="template.name" :value="template.name">
              {{ template.name }}
            </option>
          </select>
        </label>
        <button class="primary summary-generate" type="button" :disabled="store.summary.busy" @click="store.generateSummary">
          <Sparkles :size="17" />
          {{ store.summary.busy ? '生成中' : '生成总结' }}
        </button>
      </div>

      <div class="summary-path-row">
        <LatestWorkerResultSelect />
        <label>
          <span>输入稿</span>
          <input v-model="store.markdown.path" type="text" readonly />
        </label>
        <label>
          <span>总结输出路径</span>
          <input v-model="store.summary.outputPath" type="text" />
        </label>
        <button type="button" :disabled="!store.summary.output" @click="store.saveSummary">
          <Save :size="17" />
          保存总结
        </button>
      </div>

      <label class="summary-prompt">
        <span>Prompt</span>
        <textarea v-model="store.summary.prompt" rows="5" />
      </label>
    </div>

    <div class="summary-result-grid">
      <section class="panel summary-result-pane">
        <header class="panel-header compact">
          <div>
            <h2>输出结果</h2>
            <p>可直接修改生成的 Markdown 内容。</p>
          </div>
        </header>
        <textarea v-model="store.summary.output" class="summary-editor" placeholder="生成的总结将显示在这里。" />
      </section>

      <section class="panel summary-result-pane">
        <header class="panel-header compact">
          <div>
            <h2>预览</h2>
            <p>实时渲染当前输出结果。</p>
          </div>
        </header>
        <article class="markdown-body summary-preview" v-html="previewHtml" />
      </section>
    </div>
  </section>
</template>
