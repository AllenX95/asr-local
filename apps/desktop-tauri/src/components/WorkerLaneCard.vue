<script setup lang="ts">
import { Pause, Play, Square } from '@lucide/vue';
import type { PropType } from 'vue';

interface WorkerLaneState {
  id: number;
  jobId: string;
  sourcePath: string;
  stage: string;
  detail: string;
  progress: number;
  busy: boolean;
  paused: boolean;
  error: string;
}

defineProps({
  lane: {
    type: Object as PropType<WorkerLaneState>,
    required: true
  }
});

const emit = defineEmits<{
  pause: [id: number];
  resume: [id: number];
  terminate: [id: number];
}>();
</script>

<template>
  <article class="lane-card" :class="{ busy: lane.busy, failed: Boolean(lane.error) }">
    <header>
      <div>
        <strong>Worker {{ lane.id }}</strong>
        <span>{{ lane.jobId || '空闲' }}</span>
      </div>
      <div class="lane-actions">
        <button
          class="icon-button"
          type="button"
          title="暂停"
          :disabled="!lane.busy || lane.paused"
          @click="emit('pause', lane.id)"
        >
          <Pause :size="16" />
        </button>
        <button
          class="icon-button"
          type="button"
          title="恢复"
          :disabled="!lane.busy || !lane.paused"
          @click="emit('resume', lane.id)"
        >
          <Play :size="16" />
        </button>
        <button
          class="icon-button danger"
          type="button"
          title="终止"
          :disabled="!lane.busy"
          @click="emit('terminate', lane.id)"
        >
          <Square :size="15" />
        </button>
      </div>
    </header>
    <div class="progress-track">
      <span :style="{ width: `${Math.round(lane.progress * 100)}%` }" />
    </div>
    <p>{{ lane.detail }}</p>
    <small>{{ lane.sourcePath || '没有正在处理的音频。' }}</small>
  </article>
</template>
