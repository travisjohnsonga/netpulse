// Minimal type shim — swagger-ui-react ships no bundled types.
declare module 'swagger-ui-react' {
  import type { ComponentType } from 'react'
  interface SwaggerUIProps {
    spec?: object
    url?: string
    docExpansion?: 'list' | 'full' | 'none'
    defaultModelsExpandDepth?: number
    tryItOutEnabled?: boolean
  }
  const SwaggerUI: ComponentType<SwaggerUIProps>
  export default SwaggerUI
}

declare module 'swagger-ui-react/swagger-ui.css'
