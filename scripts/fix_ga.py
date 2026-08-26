"""Fix the GA syntax error in workora_app.py"""
import re

with open("scripts/workora_app.py", "r", encoding="utf-8") as f:
    content = f.read()

# Remove all broken GA-related lines
lines = content.split("\n")
clean = []
skip = False
for line in lines:
    stripped = line.strip()
    if "ga_code" in stripped or ("google" in stripped.lower() and "gtag" in stripped.lower()) or "ga_script" in stripped:
        continue
    if stripped.startswith('ga_code'):
        continue
    clean.append(line)

content = "\n".join(clean)

# Now add a simple GA constant before seo_meta
old = '    seo_meta = f"""'
replacement = '''    # Google Analytics - replace G-XXXXXXXXXX with your actual ID
    ga_script = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>\\n<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag(\"js\",new Date());gtag(\"config\",\"G-XXXXXXXXXX\");</script>\\n'
    seo_meta = f"""'''

content = content.replace(old, replacement, 1)

with open("scripts/workora_app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Fixed GA syntax")
