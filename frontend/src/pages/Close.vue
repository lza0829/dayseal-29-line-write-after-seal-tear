<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const preview = ref(null)
const declared = ref(0)
const batches = ref([])
const conflicts = ref([])
const reason = ref('')
const msg = ref('')
const err = ref('')

async function load() {
  preview.value = await api.closePreview()
  if (!preview.value.sealed) declared.value = preview.value.system_total
  batches.value = await api.batches()
  conflicts.value = await api.conflicts(preview.value.business_day)
}

const currentBatch = computed(() => preview.value?.current_batch || null)
const integrity = computed(() => preview.value?.integrity || null)
const batchNoById = computed(() => {
  const m = {}
  for (const b of preview.value?.history || []) m[b.id] = b.batch_no
  for (const b of batches.value) m[b.id] = b.batch_no
  return m
})

async function seal() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.seal({
      business_day: preview.value.business_day,
      declared_cash: declared.value,
    })
    msg.value = `已封账 ${r.batch_no}：系统 ${r.system_total.toFixed(2)} / 实盘 ${r.declared_cash.toFixed(2)} / 长短款 ${r.variance.toFixed(2)}`
    declared.value = r.declared_cash
    await load()
  } catch (e) { err.value = e.message }
}

async function reopen() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.reopen({ business_day: preview.value.business_day, reason: reason.value })
    const nos = r.voided_batches.map((b) => b.batch_no).join('、')
    msg.value = `已解封：${nos} 作废（${r.reason}），营业日可继续改流水；重新封账将生成新批号`
    reason.value = ''
    await load()
  } catch (e) { err.value = e.message }
}

async function repair() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.repair({ business_day: preview.value.business_day })
    msg.value = r.count
      ? `已修复 ${r.count} 个封账日：有效集合与批头已恢复冻结快照`
      : '所有封账日的有效集合与批头均与指纹一致'
    await load()
  } catch (e) { err.value = e.message }
}

function batchState(b) {
  if (b.status === 'sealed') return { text: '现行已封', cls: 'ok' }
  if (b.status === 'voided') return { text: '已作废', cls: 'bad' }
  return { text: b.status, cls: 'warn' }
}

onMounted(load)
</script>

<template>
  <h2>日结封账</h2>
  <div v-if="preview" class="panel">
    <p>
      营业日 {{ preview.business_day }} · 笔数 {{ preview.line_count }} · 系统合计 {{ preview.system_total.toFixed(2) }}
      <span class="tag" :class="preview.sealed ? 'ok' : 'warn'">{{ preview.sealed ? '已封' : '未封' }}</span>
      <template v-if="currentBatch">· 现行批次 {{ currentBatch.batch_no }}</template>
    </p>

    <p v-if="integrity && integrity.sealed" class="msg" :class="integrity.consistent ? '' : 'err'">
      钉扎校验：批头指纹 {{ integrity.header_fingerprint.slice(0, 12) }}… ·
      有效集合 {{ integrity.consistent ? '与冻结快照一致' : '已偏离冻结快照' }}
      （快照 {{ integrity.snapshot_line_count }} 笔 / 现行 {{ integrity.live_line_count }} 笔）
      <button v-if="!integrity.consistent" class="btn ghost small" @click="repair">修复并回滚</button>
    </p>

    <div v-if="preview.sealed" class="row">
      <label>解封原因（必填，留档）
        <input v-model="reason" placeholder="如：漏记一笔现金流水" />
      </label>
      <button class="btn" :disabled="!reason.trim()" @click="reopen">解封（作废 {{ currentBatch?.batch_no }}）</button>
      <button class="btn ghost" @click="load">刷新</button>
    </div>
    <p v-if="preview.sealed" class="msg">
      解封会把现行批次改为 voided（原合计、明细、快照一律不改写），并解除该日写锁；补录后重新封账将生成新批号，长短款以新封为准。
      封账期间所有旁路写入（改金额/导入/冲正/改日/解款）一律拒写并记冲突审计。
    </p>

    <div v-else class="row">
      <label>实盘现金 <input type="number" step="0.01" v-model.number="declared" /></label>
      <button class="btn" @click="seal">封账</button>
      <button class="btn ghost" @click="load">刷新</button>
    </div>
  </div>
  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>

  <div v-if="preview && preview.history.length" class="panel">
    <h2>当前营业日批次</h2>
    <table>
      <thead><tr><th>批次</th><th>系统</th><th>实盘</th><th>差额</th><th>状态</th><th>作废原因</th><th>被新批取代</th></tr></thead>
      <tbody>
        <tr v-for="b in preview.history" :key="b.id">
          <td>{{ b.batch_no }}</td>
          <td>{{ b.system_total.toFixed(2) }}</td>
          <td>{{ b.declared_cash.toFixed(2) }}</td>
          <td>{{ b.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="batchState(b).cls">{{ batchState(b).text }}</span></td>
          <td>{{ b.void_reason || '—' }}</td>
          <td>{{ b.superseded_by_batch_id ? (batchNoById[b.superseded_by_batch_id] || '#' + b.superseded_by_batch_id) : '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h2>封后写入冲突审计</h2>
    <p class="msg">任何旁路写入尝试都会留下一条记录：被拒绝（rejected）或已回滚（rolled_back）。</p>
    <table v-if="conflicts.length">
      <thead><tr><th>时间</th><th>营业日</th><th>入口</th><th>处置</th><th>详情</th></tr></thead>
      <tbody>
        <tr v-for="c in conflicts" :key="c.id">
          <td>{{ c.created_at }}</td>
          <td>{{ c.business_day }}</td>
          <td>{{ c.path }}</td>
          <td><span class="tag" :class="c.outcome === 'rejected' ? 'warn' : 'bad'">{{ c.outcome }}</span></td>
          <td class="detail">{{ c.detail }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="msg">无冲突记录。</p>
  </div>

  <div class="panel">
    <h2>全部封账批次</h2>
    <table>
      <thead><tr><th>批次</th><th>营业日</th><th>系统</th><th>实盘</th><th>差额</th><th>状态</th><th>封账时间</th></tr></thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td>{{ b.batch_no }}</td><td>{{ b.business_day }}</td>
          <td>{{ b.system_total.toFixed(2) }}</td><td>{{ b.declared_cash.toFixed(2) }}</td>
          <td>{{ b.variance.toFixed(2) }}</td>
          <td><span class="tag" :class="batchState(b).cls">{{ batchState(b).text }}</span></td>
          <td>{{ b.sealed_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

