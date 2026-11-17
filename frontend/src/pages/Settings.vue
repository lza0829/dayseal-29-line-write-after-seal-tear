<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const day = ref('')
const msg = ref('')
const err = ref('')

onMounted(async () => {
  const d = await api.getDay()
  day.value = d.business_day
})

async function save() {
  msg.value = ''; err.value = ''
  try {
    await api.setDay(day.value)
    msg.value = '已更新营业日'
  } catch (e) { err.value = e.message }
}
</script>

<template>
  <h2>设置</h2>
  <div class="row">
    <label>营业日 <input v-model="day" placeholder="YYYY-MM-DD" /></label>
    <button class="btn" @click="save">保存</button>
  </div>
  <p v-if="msg" class="msg">{{ msg }}</p>
  <p v-if="err" class="msg err">{{ err }}</p>
</template>
