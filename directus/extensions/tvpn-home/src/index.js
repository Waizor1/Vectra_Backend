import { defineModule } from '@directus/extensions-sdk';

import ModuleHome from './module.vue';

export default defineModule({
  id: 'tvpn-home',
  name: 'Главная',
  icon: 'space_dashboard',
  color: '#3B82F6',
  routes: [
    {
      path: '',
      component: ModuleHome,
    },
  ],
});

