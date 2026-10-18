import { readFileSync } from 'node:fs';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy to the Core API (VITE_BACKEND=api). The proxy adds the homeowner token on the server side, so the
// browser never holds a token (EventSource can't send one anyway). The token comes from PENJAGA_API_TOKEN, or
// from the Core API's dev token file, read on every request so the API can be started after the app.
function homeownerToken(env) {
  if (env.PENJAGA_API_TOKEN) return env.PENJAGA_API_TOKEN;
  const file = env.PENJAGA_API_TOKENS_FILE || 'var/api-tokens.json';
  try {
    return JSON.parse(readFileSync(file, 'utf-8')).homeowner ?? '';
  } catch {
    return '';
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ''); // all variables: the token stays out of the client bundle
  return {
    plugins: [react()],
    build: { target: 'es2022', chunkSizeWarningLimit: 1500 },
    server: {
      proxy: {
        '/api': {
          target: env.PENJAGA_API_URL || 'http://127.0.0.1:8705',
          changeOrigin: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (req) => {
              const token = homeownerToken(env);
              if (token) req.setHeader('Authorization', `Bearer ${token}`);
            });
          },
        },
      },
    },
  };
});
