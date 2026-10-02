import os, signal, subprocess, tempfile, resource
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from app.common import case_path
app = FastAPI()
class Command(BaseModel):
    case_id: str
    command: str = Field(min_length=1, max_length=20000)
    timeout: int = Field(default=25, ge=1, le=60)
def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
def execute(command, cwd, timeout=25):
    # File-backed output avoids unbounded subprocess pipe buffering.
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(["/bin/bash", "-c", command], cwd=cwd,
            stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=limits, env={"PATH":os.environ["PATH"], "HOME":"/tmp", "TERM":"dumb"})
        timed_out=False
        try: p.wait(timeout=timeout)
        except subprocess.TimeoutExpired: timed_out=True
        finally:
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            p.wait()
        out.seek(0)
        raw=out.read(12001)
    return {"exit_code":p.returncode, "timed_out":timed_out,
        "output":raw[:12000].decode("utf-8",errors="replace"), "truncated":len(raw)>12000}
@app.post("/run")
def run(req: Command):
    try: folder=case_path(req.case_id)/"work"
    except ValueError as e: raise HTTPException(400,str(e))
    if not folder.is_dir(): raise HTTPException(404,"Case not found")
    return execute(req.command, folder, req.timeout)
