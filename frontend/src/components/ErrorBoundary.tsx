import { Component, type ErrorInfo, type ReactNode } from "react";

/** Shows an error card instead of a blank page when a view fails to render. Reset by changing ``resetKey``. */
export default class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("View failed to render", error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="border border-rose-200 bg-rose-50 rounded-xl p-4 text-sm text-rose-900">
        <div className="font-semibold">This view could not be displayed.</div>
        <div className="mt-1">The rest of the app still works; try another tab or reload the page. If it keeps happening,
          report this message: <code className="break-all">{this.state.error.message}</code></div>
      </div>
    );
  }
}
