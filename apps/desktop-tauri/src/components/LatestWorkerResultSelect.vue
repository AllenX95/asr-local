<script setup lang="ts">
import { computed } from 'vue';
import { useAppStore } from '../stores/appStore';

const store = useAppStore();

const lanes = computed(() => Object.values(store.lanes).sort((left, right) => left.id - right.id));

const selectedLaneId = computed(() => {
  const currentPath = normalizePath(store.markdown.path);
  const lane = lanes.value.find(
    (item) => item.latestResult && normalizePath(item.latestResult.md_path) === currentPath
  );
  return lane ? String(lane.id) : '';
});

function resultName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function normalizePath(path: string) {
  return path.replace(/\\/g, '/').toLocaleLowerCase();
}

function selectResult(event: Event) {
  const laneId = Number((event.target as HTMLSelectElement).value);
  if (Number.isInteger(laneId)) {
    void store.loadLatestWorkerResult(laneId);
  }
}
</script>

<template>
  <label class="worker-result-select">
    <span>Worker 最新识别结果</span>
    <select :value="selectedLaneId" @change="selectResult">
      <option value="" disabled>选择一个已完成的结果</option>
      <option
        v-for="lane in lanes"
        :key="lane.id"
        :value="lane.id"
        :disabled="!lane.latestResult"
      >
        Worker {{ lane.id }} ·
        {{ lane.latestResult ? resultName(lane.latestResult.md_path) : '暂无完成结果' }}
      </option>
    </select>
  </label>
</template>
