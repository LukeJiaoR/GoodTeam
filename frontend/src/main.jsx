import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './index.css'
import './styles/product.css'
import './styles/neumorphic.css'
import './styles/neumorphicPages.css'
import './styles/neumorphicDocuments.css'
import './features/team/teamNeumorphic.css'
import { createProductRoutes } from './app/AppRouter.jsx'

const router = createBrowserRouter(createProductRoutes())

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
