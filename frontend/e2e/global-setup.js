import { request } from '@playwright/test';

export default async function globalSetup(config) {
  const client = await request.newContext({ baseURL: config.projects[0].use.baseURL });
  try {
    const response = await client.post('/api/settings', {
      data: { worker_backend: 'fake', model_profile: 'balanced' },
    });
    if (!response.ok()) throw new Error(`Fake Worker setup failed: ${await response.text()}`);
  } finally {
    await client.dispose();
  }
}
