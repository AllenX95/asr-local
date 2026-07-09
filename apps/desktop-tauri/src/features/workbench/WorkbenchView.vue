<script setup lang="ts">
import { FolderOpen, Play, RotateCw } from '@lucide/vue';
import WorkerLaneCard from '../../components/WorkerLaneCard.vue';
import { useAppStore } from '../../stores/appStore';

const store = useAppStore();
</script>

<template>
  <section class="view-grid workbench-grid">
    <div class="panel form-panel">
      <header class="panel-header">
        <div>
          <h1>工作台</h1>
          <p>音频导入、任务参数和 Python worker 进度。</p>
        </div>
        <button
          class="primary"
          type="button"
          :disabled="!store.initialized || store.submitting"
          @click="store.submitTranscription"
        >
          <Play :size="17" />
          开始转写
        </button>
      </header>

      <div class="field-row">
        <label>
          <span>音频文件</span>
          <div class="input-action">
            <input v-model="store.workbench.sourcePath" type="text" />
            <button type="button" title="选择音频" @click="store.chooseAudioFile">
              <FolderOpen :size="17" />
            </button>
          </div>
        </label>
      </div>

      <div class="two-col">
        <label>
          <span>输出目录</span>
          <div class="input-action">
            <input v-model="store.workbench.outputDir" type="text" />
            <button type="button" title="选择目录" @click="store.chooseOutputDir">
              <FolderOpen :size="17" />
            </button>
          </div>
        </label>
        <label>
          <span>输出文件名</span>
          <input v-model="store.workbench.outputFileName" type="text" />
        </label>
      </div>

      <div class="two-col">
        <label>
          <span>ASR 后端</span>
          <select v-model="store.workbench.asrBackend">
            <option value="local">本地模型</option>
            <option value="cloud">云端 API</option>
          </select>
        </label>
        <label>
          <span>云端 Profile</span>
          <select v-model="store.workbench.asrProfileName" :disabled="store.workbench.asrBackend !== 'cloud'">
            <option value="">未选择</option>
            <option v-for="profile in store.asrProfiles.profiles" :key="profile.name" :value="profile.name">
              {{ profile.name }} · {{ profile.model }}
            </option>
          </select>
        </label>
      </div>

      <div class="option-strip">
        <label>
          <span>语言模式</span>
          <select v-model="store.workbench.languageMode">
            <option value="auto">自动检测</option>
            <option value="fixed">固定语言</option>
          </select>
        </label>
        <label>
          <span>固定语言</span>
          <input v-model="store.workbench.fixedLanguage" :disabled="store.workbench.languageMode !== 'fixed'" />
        </label>
        <label class="check">
          <input v-model="store.workbench.speakerDiarization" type="checkbox" />
          <span>说话人分离</span>
        </label>
        <label class="check">
          <input v-model="store.workbench.keepFillers" type="checkbox" />
          <span>保留语气词</span>
        </label>
        <label class="check">
          <input v-model="store.workbench.autoPunctuation" type="checkbox" />
          <span>自动标点</span>
        </label>
      </div>

      <label>
        <span>背景信息</span>
        <textarea v-model="store.workbench.contextText" rows="5" />
      </label>

      <div class="two-col">
        <label>
          <span>重点术语</span>
          <textarea v-model="store.workbench.termsText" rows="6" placeholder="每行一个术语" />
        </label>
        <label>
          <span>替换规则</span>
          <textarea v-model="store.workbench.replacementsText" rows="6" placeholder="错词 => 正确词" />
        </label>
      </div>
    </div>

    <aside class="side-stack">
      <div class="panel">
        <header class="panel-header compact">
          <h2>Worker lanes</h2>
          <button class="ghost" type="button" title="刷新历史" @click="store.refreshHistory">
            <RotateCw :size="16" />
          </button>
        </header>
        <div class="lane-stack">
          <WorkerLaneCard
            v-for="lane in Object.values(store.lanes)"
            :key="lane.id"
            :lane="lane"
            @pause="store.pauseLane"
            @resume="store.resumeLane"
            @terminate="store.terminateLane"
          />
        </div>
      </div>

      <div class="panel recent-events">
        <header class="panel-header compact">
          <h2>最近事件</h2>
        </header>
        <ol>
          <li v-for="event in store.jobs.slice(0, 8)" :key="`${event.job_id}-${event.stage}-${event.processed_ms}`">
            <strong>{{ event.stage }}</strong>
            <span>{{ event.detail || event.error }}</span>
          </li>
        </ol>
      </div>
    </aside>
  </section>
</template>
