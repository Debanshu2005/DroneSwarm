with open('mobile/src/components/ErrorBoundary.jsx', 'r') as f:
    content = f.read()

content = content.replace("return (\\n        <div className=\\\"view-container fade-in\\\" style={{display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>", "if (this.props.fallback) return this.props.fallback;\\n      return (\\n        <div className=\\\"view-container fade-in\\\" style={{display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>")

with open('mobile/src/components/ErrorBoundary.jsx', 'w') as f:
    f.write(content)

