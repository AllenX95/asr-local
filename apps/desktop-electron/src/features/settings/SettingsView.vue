<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { Save, Stethoscope, Trash2 } from '@lucide/vue';
import { useAppStore } from '../../stores/appStore';
import type { AsrCloudProfile, SummaryProfile } from '../../ipc/workerTypes';

const store = useAppStore();
const cloudAsrEnabled = false;

const modelDraft = reactive({
  modelRoot: '',
  qwenPath: '',
  pyannotePath: ''
});

const profileDraft = reactive<SummaryProfile>({
  name: '',
  base_url: '',
  model: '',
  api_key: ''
});

const asrProfileDraft = reactive<AsrCloudProfile>({
  name: '',
  base_url: '',
  model: '',
  api_key: ''
});

const templateDraft = reactive({
  name: '',
  prompt: ''
});

watch(
  () => store.settings.models,
  (models) => {
    if (!models) {
      return;
    }
    modelDraft.modelRoot = models.raw.model_root;
    modelDraft.qwenPath = models.raw.qwen3_asr_1_7b.path;
    modelDraft.pyannotePath = models.raw.pyannote_speaker_diarization.path;
  },
  { immediate: true }
);

watch(
  () => store.workbench.asrProfileName,
  (name) => {
    const profile = store.asrProfiles.profiles.find((item) => item.name === name);
    if (!profile) {
      return;
    }
    Object.assign(asrProfileDraft, profile);
  },
  { immediate: true }
);

watch(
  () => store.summary.selectedProfileName,
  (name) => {
    const profile = store.summaryProfiles.profiles.find((item) => item.name === name);
    if (!profile) {
      return;
    }
    Object.assign(profileDraft, profile);
  },
  { immediate: true }
);

watch(
  () => store.summary.selectedTemplateName,
  (name) => {
    const template = store.summaryTemplates.find((item) => item.name === name);
    if (!template) {
      return;
    }
    templateDraft.name = template.name;
    templateDraft.prompt = template.prompt;
  },
  { immediate: true }
);

const healthRows = computed(() =>
  Object.entries(store.settings.health || {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value)
  }))
);
</script>

<template>
  <section class="view-grid settings-grid">
    <div class="panel form-panel">
      <header class="panel-header">
        <div>
          <h1>设置</h1>
          <p>模型路径、Python 检测、总结 API 和模板。</p>
        </div>
      </header>

      <section class="settings-section">
        <header class="panel-header compact">
          <h2>模型路径</h2>
          <button
            class="primary"
            type="button"
            @click="store.saveModelPaths(modelDraft.modelRoot, modelDraft.qwenPath, modelDraft.pyannotePath)"
          >
            <Save :size="16" />
            保存
          </button>
        </header>
        <label>
          <span>模型根目录</span>
          <input v-model="modelDraft.modelRoot" type="text" />
        </label>
        <p class="setting-hint">本地链路：Pyannote + Qwen3-ASR</p>
        <label>
          <span>Qwen3-ASR</span>
          <input v-model="modelDraft.qwenPath" type="text" />
          <small :class="{ ok: store.settings.models?.qwen_exists }">
            {{ store.settings.models?.qwen_exists ? '路径存在' : '路径未检测到' }}
          </small>
        </label>
        <label>
          <span>pyannote</span>
          <input v-model="modelDraft.pyannotePath" type="text" />
          <small :class="{ ok: store.settings.models?.pyannote_exists }">
            {{ store.settings.models?.pyannote_exists ? '路径存在' : '路径未检测到' }}
          </small>
        </label>
      </section>

      <section class="settings-section">
        <header class="panel-header compact">
          <h2>Python Worker</h2>
          <button type="button" :disabled="store.settings.checkingHealth" @click="store.checkWorkerHealth">
            <Stethoscope :size="16" />
            检测
          </button>
        </header>
        <dl class="health-list">
          <template v-for="row in healthRows" :key="row.key">
            <dt>{{ row.key }}</dt>
            <dd>{{ row.value }}</dd>
          </template>
        </dl>
        <p v-if="store.settings.healthError" class="error-text">{{ store.settings.healthError }}</p>
      </section>
    </div>

    <div class="panel form-panel">
      <section v-if="cloudAsrEnabled" class="settings-section">
        <header class="panel-header compact">
          <h2>云端 ASR Profile</h2>
          <div class="toolbar">
            <button class="primary" type="button" @click="store.saveAsrProfile(asrProfileDraft)">
              <Save :size="16" />
              保存
            </button>
            <button type="button" :disabled="!asrProfileDraft.name" @click="store.deleteAsrProfile(asrProfileDraft.name)">
              <Trash2 :size="16" />
            </button>
          </div>
        </header>
        <select v-model="store.workbench.asrProfileName">
          <option v-for="profile in store.asrProfiles.profiles" :key="profile.name" :value="profile.name">
            {{ profile.name }}
          </option>
        </select>
        <label>
          <span>名称</span>
          <input v-model="asrProfileDraft.name" type="text" />
        </label>
        <label>
          <span>Base URL</span>
          <input v-model="asrProfileDraft.base_url" type="text" placeholder="https://api.example.com/v1" />
        </label>
        <label>
          <span>Model</span>
          <input v-model="asrProfileDraft.model" type="text" />
        </label>
        <label>
          <span>API Key</span>
          <input v-model="asrProfileDraft.api_key" type="password" />
        </label>
      </section>

      <section class="settings-section">
        <header class="panel-header compact">
          <h2>总结 Profile</h2>
          <div class="toolbar">
            <button class="primary" type="button" @click="store.saveProfile(profileDraft)">
              <Save :size="16" />
              保存
            </button>
            <button type="button" :disabled="!profileDraft.name" @click="store.deleteProfile(profileDraft.name)">
              <Trash2 :size="16" />
            </button>
          </div>
        </header>
        <select v-model="store.summary.selectedProfileName">
          <option v-for="profile in store.summaryProfiles.profiles" :key="profile.name" :value="profile.name">
            {{ profile.name }}
          </option>
        </select>
        <label>
          <span>名称</span>
          <input v-model="profileDraft.name" type="text" />
        </label>
        <label>
          <span>Base URL</span>
          <input v-model="profileDraft.base_url" type="text" />
        </label>
        <label>
          <span>Model</span>
          <input v-model="profileDraft.model" type="text" />
        </label>
        <label>
          <span>API Key</span>
          <input v-model="profileDraft.api_key" type="password" />
        </label>
      </section>

      <section class="settings-section">
        <header class="panel-header compact">
          <h2>Prompt 模板</h2>
          <div class="toolbar">
            <button class="primary" type="button" @click="store.saveTemplate(templateDraft.name, templateDraft.prompt)">
              <Save :size="16" />
              保存
            </button>
            <button type="button" :disabled="!templateDraft.name" @click="store.deleteTemplate(templateDraft.name)">
              <Trash2 :size="16" />
            </button>
          </div>
        </header>
        <select v-model="store.summary.selectedTemplateName">
          <option v-for="template in store.summaryTemplates" :key="template.name" :value="template.name">
            {{ template.name }}
          </option>
        </select>
        <label>
          <span>名称</span>
          <input v-model="templateDraft.name" type="text" />
        </label>
        <label>
          <span>Prompt</span>
          <textarea v-model="templateDraft.prompt" rows="9" />
        </label>
      </section>
    </div>
  </section>
</template>
