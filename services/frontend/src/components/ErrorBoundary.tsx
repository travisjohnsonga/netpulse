import { Component, type ReactNode, type ErrorInfo } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="flex items-center justify-center h-64 p-6">
            <div className="text-center max-w-md">
              <p className="text-4xl mb-4">⚠️</p>
              <h2 className="text-lg font-semibold text-gray-800 mb-2">Something went wrong</h2>
              <p className="text-sm text-gray-500 mb-4 font-mono bg-gray-50 rounded p-3 text-left break-all">
                {this.state.error.message}
              </p>
              <button
                onClick={() => this.setState({ error: null })}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                Try again
              </button>
            </div>
          </div>
        )
      )
    }
    return this.props.children
  }
}
