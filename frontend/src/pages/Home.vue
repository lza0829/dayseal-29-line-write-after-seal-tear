<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const dash = ref(null)
const err = ref('')

onMounted(async () => {
  try { dash.value = await api.dashboard() }
  catch (e) { err.value = e.message }
})

/* fence-29 */

function fenceHint(d) {
  if (!d) return ''
  return [d.pending_fence, d.seal_allowed, d.open_variance_count].join('|')
}

</script>

<template>
  <section class="hero">
    <h1>日封台</h1>
    <p>把当日收银流水钉进营业日封账：系统合计与实盘对齐；需要补录时解封作废旧批、改完流水再封新批。</p>
  </section>
  <p v-if="err" class="msg err">{{ err }}</p>
  <div class="stats" v-if="dash">
    <div class="stat"><div class="label">营业日</div><div class="value">{{ dash.business_day }}</div></div>
    <div class="stat"><div class="label">今日流水笔数</div><div class="value">{{ dash.line_count_today }}</div></div>
    <div class="stat"><div class="label">系统合计</div><div class="value">{{ dash.system_total_today.toFixed(2) }}</div></div>
    <div class="stat"><div class="label">封账状态</div><div class="value">{{ dash.sealed_today ? '已封' : '未封' }}</div></div>
    <div class="stat"><div class="label">现行已封天数</div><div class="value">{{ dash.sealed_day_count }}</div></div>
    <div class="stat"><div class="label">现行批次</div><div class="value">{{ dash.close_batch_count }}</div></div>
    <div class="stat"><div class="label">已作废批次</div><div class="value">{{ dash.voided_batch_count }}</div></div>
    <div class="stat"><div class="label">待处置长短款</div><div class="value">{{ dash.open_variance_count }}</div></div>
  </div>
</template>
