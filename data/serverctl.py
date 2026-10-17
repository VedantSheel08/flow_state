import subprocess, sys, os, time, signal
from pathlib import Path
PID = Path(__file__).with_name("server.pid")
py = str(Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe")
def stop():
    if PID.exists():
        pid = PID.read_text().strip()
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
        PID.unlink()
def start(port="8011"):
    stop()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    log = open(Path(__file__).with_name("server.log"), "w")
    p = subprocess.Popen([py, "-m", "flowstate.cli", "serve", "--port", port], cwd=str(Path(__file__).resolve().parents[1] / "backend"), stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    PID.write_text(str(p.pid))
    time.sleep(5)
    print("started pid", p.pid)
if __name__ == "__main__":
    {"start": start, "stop": stop}[sys.argv[1]](*sys.argv[2:])
