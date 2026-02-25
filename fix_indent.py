with open("flask_app/worker.py", "r") as f:
    lines = f.readlines()

for i in range(57, 143):
    if lines[i].startswith("    ") and lines[i].strip():
        lines[i] = lines[i][4:]

with open("flask_app/worker.py", "w") as f:
    f.writelines(lines)
