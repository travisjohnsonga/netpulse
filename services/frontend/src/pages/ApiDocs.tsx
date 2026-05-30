import { lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import 'swagger-ui-react/swagger-ui.css'
import { api } from '../api/client'

// Code-split the (large) Swagger UI bundle so it only loads on this route.
const SwaggerUI = lazy(() => import('swagger-ui-react'))

// Fetch the OpenAPI schema through the shared axios client so the JWT is
// attached automatically — the schema endpoint requires authentication.
async function fetchSchema(): Promise<object> {
  const { data } = await api.get('/schema/', { params: { format: 'json' } })
  return data
}

export default function ApiDocs() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['openapi-schema'],
    queryFn: fetchSchema,
    staleTime: 5 * 60 * 1000,
  })

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">API Docs</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Interactive OpenAPI reference for the NetPulse API. Authenticated with your current session.
        </p>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-24">
          <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {isError && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-400">
          Couldn't load the API schema. Make sure the API is running and you're signed in.
        </div>
      )}

      {data && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <Suspense fallback={
            <div className="flex items-center justify-center py-24">
              <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          }>
            <SwaggerUI spec={data} docExpansion="none" tryItOutEnabled />
          </Suspense>
        </div>
      )}
    </div>
  )
}
