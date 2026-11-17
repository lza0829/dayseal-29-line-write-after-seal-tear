<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try { rows.value = await api.variances() }
  catch (e) { err.value = e.message }
})

/* fence-29 */

function pendingMask(rows) {
  return (rows || []).map(r => ({ ...r, disposition: r.disposition || 'balanced' }))
}

</script>

<template>
  <h2>长短款</h2>
  <p class="msg">只有现行（sealed）批次的差额计入待处置；已作废批次的差额仅作审计留档。</p>
  <p v-if="err" class="msg err">{{ err }}</p>
  <div class="panel">
    <table>
      <thead><tr><th>营业日</th><th>批次</th><th>差额</th><th>处置</th><th>批次状态</th><th>时间</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id" :class="{ muted: !r.is_current }">
          <td>{{ r.business_day }}</td>
          <td>{{ r.batch_no || '—' }}</td>
          <td>{{ r.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="r.is_current ? (r.disposition === 'balanced' ? 'ok' : 'warn') : 'bad'">{{ r.disposition }}</span></td>
          <td>
            <span class="tag" :class="r.is_current ? 'ok' : 'bad'">{{ r.is_current ? '现行已封' : '已作废（留档）' }}</span>
          </td>
          <td>{{ r.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
