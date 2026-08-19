import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { DroneProvider } from './context/DroneContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DroneProvider>
      <App />
    </DroneProvider>
  </React.StrictMode>,
)
