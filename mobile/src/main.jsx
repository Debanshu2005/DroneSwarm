import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DroneProvider } from './context/DroneContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
    <DroneProvider>
      <App />
    </DroneProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)
