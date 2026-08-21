with open('mobile/index.html', 'r') as f:
    content = f.read()

test_div = """    <div id="startup-diagnostic" style="position: fixed; top: 10px; right: 10px; background: yellow; color: black; z-index: 2147483647; font-size: 20px; font-weight: bold; padding: 10px;">
        HTML LOADED
    </div>
    <div id="root"></div>"""

if "startup-diagnostic" not in content:
    content = content.replace('<div id="root"></div>', test_div)

with open('mobile/index.html', 'w') as f:
    f.write(content)
