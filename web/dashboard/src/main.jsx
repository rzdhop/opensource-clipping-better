import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { DialogProvider, ToastProvider } from './ui'
import './index.css'
import './ui/ui.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <DialogProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </DialogProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
