import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Uploads go browser -> this dev server -> proxy -> backend:8000, and the
 * files are videos of up to 2GB. Two separate timeouts will kill a slow
 * upload long before it finishes, and both fail in a way that leaves NO
 * entry in the backend log, because the request never completes:
 *
 *   - Node's own http server: requestTimeout defaults to 300s (Node 18+),
 *     headersTimeout to 60s. Vite does not expose these, so they are set on
 *     the underlying server here.
 *   - http-proxy's socket timeouts, set via the proxy options below.
 *
 * 0 disables each one. That is deliberate for a dev server whose whole job
 * is accepting large media, and it is what makes a 20-minute upload on a
 * slow link possible at all.
 */
const allowLongUploads = {
  name: 'allow-long-uploads',
  configureServer(server) {
    if (server.httpServer) {
      server.httpServer.requestTimeout = 0
      server.httpServer.headersTimeout = 0
      server.httpServer.timeout = 0
    }
  },
}

export default defineConfig({
  plugins: [react(), allowLongUploads],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        // 'backend' is the docker-compose service name, so it only resolves
        // inside the compose network. Set VITE_API_TARGET to run the dashboard
        // on the host against a local backend, e.g.
        //   VITE_API_TARGET=http://127.0.0.1:8000 npm run dev
        target: process.env.VITE_API_TARGET || 'http://backend:8000',
        changeOrigin: true,
        // Socket timeouts towards the backend, and for the incoming request.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
    watch: {
      usePolling: true,
    },
  },
})
