import { readFileSync } from 'node:fs';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy to the Core API (VITE_BACKEND=api). The proxy adds the homeowner token on the server side, so the
// browser never holds a token (EventSource can't send one anyway). The token comes from PENJAGA_API_TOKEN, or
// from the Core API's dev token file, read on every request so the API can be started after the app.
function apiToken(env, role) {
  if (role === 'homeowner' && env.PENJAGA_API_TOKEN) return env.PENJAGA_API_TOKEN;
  const file = env.PENJAGA_API_TOKENS_FILE || 'var/api-tokens.json';
  try {
    return JSON.parse(readFileSync(file, 'utf-8'))[role] ?? '';
  } catch {
    return '';
  }
}

// The Lab (an operator page, never linked from the homeowner app) drives the simulator, reads detection, uses the
// Core API's operator routes and withdraws notices as management. The operator token comes from the backend's dev
// token file and the management token from the Core API's; both are added here, on the dev server only.
function operatorToken(env) {
  const file = env.PENJAGA_DEV_TOKENS_FILE || 'var/dev-tokens.json';
  try {
    return JSON.parse(readFileSync(file, 'utf-8')).operator ?? '';
  } catch {
    return '';
  }
}

const withToken = (token) => (proxy) => {
  proxy.on('proxyReq', (req) => {
    const value = token();
    if (value) req.setHeader('Authorization', `Bearer ${value}`);
  });
};

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ''); // all variables: the token stays out of the client bundle
  const api = env.PENJAGA_API_URL || 'http://127.0.0.1:8705';
  return {
    plugins: [react()],
    build: { target: 'es2022', chunkSizeWarningLimit: 1500 },
    server: {
      proxy: {
        '/api': { target: api, changeOrigin: true, configure: withToken(() => apiToken(env, 'homeowner')) },
        '/lab/api': {
          target: api, changeOrigin: true, rewrite: (p) => p.replace(/^\/lab\/api/, '/api'),
          configure: withToken(() => operatorToken(env)),
        },
        '/lab/mgmt': {
          target: api, changeOrigin: true, rewrite: (p) => p.replace(/^\/lab\/mgmt/, '/api'),
          configure: withToken(() => apiToken(env, 'management')),
        },
        '/lab/sim': {
          target: env.PENJAGA_SIM_URL || 'http://127.0.0.1:8702', changeOrigin: true,
          rewrite: (p) => p.replace(/^\/lab\/sim/, '/sim'), configure: withToken(() => operatorToken(env)),
        },
        '/lab/detect': {
          target: env.PENJAGA_DETECT_URL || 'http://127.0.0.1:8703', changeOrigin: true,
          rewrite: (p) => p.replace(/^\/lab\/detect/, '/detect'), configure: withToken(() => operatorToken(env)),
        },
      },
    },
  };
});
