import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    base: '/',
    server: {
        port: 3000,
        host: true,
        proxy: {
            // In dev, proxy /api calls to the Express backend
            '/api': {
                target: 'http://localhost:4000',
                changeOrigin: true
            }
        }
    }
})
