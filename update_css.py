with open('mobile/src/index.css', 'r') as f:
    content = f.read()

new_css = """
html, body, #root {
  width: 100vw;
  height: 100vh;
  margin: 0;
  padding: 0;
  overflow: hidden;
  background-color: #F6F7F9;
}

body {
  font-family: Inter, system-ui, Avenir, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
"""

with open('mobile/src/index.css', 'w') as f:
    f.write(new_css)
print("index.css updated.")
