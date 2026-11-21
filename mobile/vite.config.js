import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.VITE_HOST ? process.env.VITE_HOST : true,
    cors: true,
    hmr: {
      clientPort: 5173
    }
  }
})
