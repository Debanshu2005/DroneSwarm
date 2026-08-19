import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="view-container fade-in" style={{display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
           <AlertTriangle size={64} style={{color: 'var(--danger)', marginBottom: '20px'}}/>
           <h2 style={{color: 'var(--danger)', marginBottom: '10px'}}>RECOVERABLE UI ERROR</h2>
           <p style={{color: 'var(--text-muted)', marginBottom: '20px', maxWidth: '400px', textAlign: 'center'}}>
              A component crashed, but the rest of the application is safe. Flight safety is maintained by PX4.
           </p>
           <div style={{background: 'rgba(0,0,0,0.5)', padding: '10px', borderRadius: '8px', marginBottom: '20px', fontSize: '0.8rem', fontFamily: 'monospace', maxWidth: '80%', overflowX: 'auto'}}>
              {this.state.error?.toString()}
           </div>
           <button className="primary-btn" onClick={() => this.setState({ hasError: false })} style={{display: 'flex', alignItems: 'center', gap: '10px'}}>
              <RefreshCw size={18}/> RELOAD VIEW
           </button>
        </div>
      );
    }
    return this.props.children;
  }
}
