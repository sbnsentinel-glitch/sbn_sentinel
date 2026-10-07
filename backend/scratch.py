with open("src/runtime/app/services/processing_orchestrator.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if '"""' in line or "'''" in line:
        print(f"{i+1}: {line.strip()}")
