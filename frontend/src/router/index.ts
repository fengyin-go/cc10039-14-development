import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import MeteringView from '@/views/MeteringView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/metering', name: 'metering', component: MeteringView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
