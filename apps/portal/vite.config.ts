import { readFileSync } from 'node:fs';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// The portal reaches the Core API through this dev proxy, which adds the management token on the server side:
// the browser never holds a token. The token comes from PENJAGA_MANAGEMENT_TOKEN, or from the Core API's dev token
// file (read per request, so the API can start after the portal).
function managementToken(env: Record<string, string>): string {
  if (env.PENJAGA_MANAGEMENT_TOKEN) return env.PENJAGA_MANAGEMENT_TOKEN;
  const file = env.PENJAGA_API_TOKENS_FILE || '../../var/api-tokens.json';
  try {
    return JSON.parse(readFileSync(file, 'utf-8')).management ?? '';
  } catch {
    return '';
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    build: { target: 'es2022' },
    server: {
      proxy: {
        '/api': {
          target: env.PENJAGA_API_URL || 'http://127.0.0.1:8705',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (req) => {
              const token = managementToken(env);
              if (token) req.setHeader('Authorization', `Bearer ${token}`);
            });
          },
        },
      },
    },
  };
});
