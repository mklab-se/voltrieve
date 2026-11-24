import { Component, type ReactNode } from "react";

/** When the car breaks, the driver gets a plain text instead of an empty page. */
export class ErrorBoundary extends Component<{ children?: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="gate">
        <div className="gate-card" role="alert">
          <p className="eyebrow">Voltrieve · Spark</p>
          <h1>Something went wrong in the car.</h1>
          <p>Reload the page to drive on.</p>
          <button type="button" className="primary" onClick={() => location.reload()}>
            Reload
          </button>
        </div>
      </main>
    );
  }
}
