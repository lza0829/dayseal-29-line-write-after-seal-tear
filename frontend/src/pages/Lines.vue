<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const tills = ref([])
const day = ref('')
const sealed = ref(false)
const currentBatch = ref(null)
const integrity = ref(null)
const form = ref({ till_code: 'TILL-A', amount: 10, pay_method: '现金', note: '' })
const pickupForm = ref({ till_code: 'TILL-A', amount: 0, note: '' })
const msg = ref('')
const err = ref('')

async function load() {
  err.value = ''
  const d = await api.getDay()
  day.value = d.business_day
  tills.value = await api.tills()
  const p = await api.closePreview(day.value)
  sealed.value = p.sealed
  currentBatch.value = p.current_batch
  integrity.value = p.integrity
  rows.value = await api.lines(day.value)
}

async function run(fn) {
  msg.value = ''; err.value = ''
  try {
    await fn()
    await load()
  } catch (e) { err.value = e.message; await load() }
}

const add = () => run(async () => {
  await api.createLine({ ...form.value, business_day: day.value })
  msg.value = '已登记流水'
})

const save = (row) => run(async () => {
  await api.patchLine(row.id, { amount: row.amount, pay_method: row.pay_method, note: row.note })
  msg.value = `${row.line_no} 已修改`
})

const remove = (row) => run(async () => {
  await api.deleteLine(row.id)
  msg.value = `${row.line_no} 已删除`
})

const reverse = (row) => run(async () => {
  await api.reverseLine(row.id, { reason: `冲正 ${row.line_no}` })
  msg.value = `${row.line_no} 已冲正`
})

const moveDay = (row) => run(async () => {
  const to = window.prompt('改到哪个营业日？', day.value)
  if (!to) return
  await api.moveLineDay(row.id, to)
  msg.value = `${row.line_no} 已改归属日到 ${to}`
})

const importDemo = () => run(async () => {
  await api.importLines({
    business_day: day.value,
    lines: [{ till_code: 'TILL-A', amount: 12.34, pay_method: '现金', note: '导入补录' }],
  })
  msg.value = '已导入一筆'
})

const pickup = () => run(async () => {
  await api.createPickup({ ...pickupForm.value, business_day: day.value })
  msg.value = '已登记解款'
})

const total = computed(() => rows.value.reduce((s, r) => s + Number(r.amount || 0), 0))

onMounted(load)
</script>

<template>
  <h2>收银流水</h2>
  <p class="msg">
    营业日 {{ day }} · 笔数 {{ rows.length }} · 合计 {{ total.toFixed(2) }}
    <span class="tag" :class="sealed ? 'ok' : 'warn'">{{ sealed ? '已封账 · 只读' : '未封 · 可改' }}</span>
    <template v-if="currentBatch">· 现行批次 {{ currentBatch.batch_no }}</template>
  </p>

  <p v-if="sealed" class="msg warn-text">
    该营业日已封账，所有写入入口（新增 / 修改 / 删除 / 导入 / 冲正 / 改归属日 / 解款）一律拒写。
    需要补录请先到「封账」页解封：原批作废留档，改完重新封账生成新批号。
  </p>
  <p v-if="integrity && integrity.sealed" class="msg" :class="integrity.consistent ? '' : 'err'">
    钉扎校验：批头 {{ integrity.batch_no }} ·
    {{ integrity.consistent ? '有效集合与指纹一致' : '有效集合与指纹不一致（请封账页修复）' }}
    · 快照 {{ integrity.snapshot_line_count }} 笔 / 现行 {{ integrity.live_line_count }} 笔
  </p>

  <div class="row" v-if="!sealed">
    <label>收银机
      <select v-model="form.till_code">
        <option v-for="t in tills" :key="t.till_code" :value="t.till_code">{{ t.title }}</option>
      </select>
    </label>
    <label>金额 <input type="number" step="0.01" v-model.number="form.amount" /></label>
    <label>支付方式
      <select v-model="form.pay_method">
        <option>现金</option><option>微信</option><option>支付宝</option>
      </select>
    </label>
    <button class="btn" @click="add">登记</button>
    <button class="btn ghost" @click="importDemo">导入一笔</button>
  </div>
  <div class="row" v-if="!sealed">
    <label>解款机
      <select v-model="pickupForm.till_code">
        <option v-for="t in tills" :key="t.till_code" :value="t.till_code">{{ t.title }}</option>
      </select>
    </label>
    <label>解款金额 <input type="number" step="0.01" v-model.number="pickupForm.amount" /></label>
    <button class="btn ghost" @click="pickup">登记解款</button>
  </div>
  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>
  <div class="panel">
    <table>
      <thead>
        <tr>
          <th>单号</th><th>机</th><th>时刻</th><th>金额</th><th>方式</th>
          <th v-if="!sealed">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.line_no }}</td><td>{{ r.till_code }}</td><td>{{ r.occurred_at }}</td>
          <td><input v-if="!sealed" type="number" step="0.01" v-model.number="r.amount" /><span v-else>{{ r.amount.toFixed(2) }}</span></td>
          <td>{{ r.pay_method }}</td>
          <td v-if="!sealed">
            <button class="btn ghost small" @click="save(r)">保存</button>
            <button class="btn ghost small" @click="reverse(r)">冲正</button>
            <button class="btn ghost small" @click="moveDay(r)">改日</button>
            <button class="btn ghost small" @click="remove(r)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
