"""Wrapper: pytest nel venv del progetto (git-bash non esegue i .exe
del venv con path relativo). Uso: python scripts/run_tests.py <args...>"""
import subprocess
import sys

if __name__ == "__main__":
    r = subprocess.run([r".venv\Scripts\python.exe", "-m", "pytest"] + sys.argv[1:],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(out[-4000:])
    sys.exit(r.returncode)
