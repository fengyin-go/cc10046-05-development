import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import QuoteWorkflowView from '@/views/QuoteWorkflowView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/quote-workflow', name: 'quote-workflow', component: QuoteWorkflowView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
