<script setup lang="ts">
import { ExternalLink, FileText, RefreshCw } from '@lucide/vue';
import { useAppStore } from '../../stores/appStore';

const store = useAppStore();

function formatTime(value: number) {
  if (!value) {
    return '';
  }
  return new Date(value).toLocaleString();
}

function formatSize(value: number) {
  if (value >= 1024 * 1024) {
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}
</script>

<template>
  <section class="view-column">
    <header class="view-header">
      <div>
        <h1>历史</h1>
        <p>扫描 outputs 目录中的转写稿、总结稿和最终稿。</p>
      </div>
      <button type="button" @click="store.refreshHistory">
        <RefreshCw :size="17" />
        刷新
      </button>
    </header>

    <div class="panel table-panel">
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>文件</th>
            <th>大小</th>
            <th>修改时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in store.history" :key="item.id">
            <td><span class="kind-pill">{{ item.kind }}</span></td>
            <td>
              <button class="link-button" type="button" @click="store.loadMarkdownPath(item.path)">
                <FileText :size="16" />
                {{ item.title }}
              </button>
              <small>{{ item.path }}</small>
            </td>
            <td>{{ formatSize(item.size_bytes) }}</td>
            <td>{{ formatTime(item.modified_ms) }}</td>
            <td>
              <button class="icon-button" type="button" title="定位文件" @click="store.openPath(item.path)">
                <ExternalLink :size="16" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
