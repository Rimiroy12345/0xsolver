import asyncio, json, os, re, time, uuid
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.common import ROOT, case_path
from app.engine import Engine, ROLES
app=FastAPI(title="0xsolver")
app.mount("/static",StaticFiles(directory=Path(__file__).parent/"static"),name="static")
BASE=os.getenv("LM_BASE_URL","http://host.docker.internal:1234/v1")
WORKER=os.getenv("WORKER_URL","http://worker:8001")
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
jobs={}; active=asyncio.Lock()
def read_case(cid):
    try: return json.loads((case_path(cid)/"case.json").read_text())
    except (ValueError,FileNotFoundError): raise HTTPException(404,"Case not found")
def save_case(case):
    path=case_path(case["id"])/"case.json"
    tmp=path.with_suffix(".tmp"); tmp.write_text(json.dumps(case)); tmp.replace(path)
@app.get("/")
def index(): return FileResponse(Path(__file__).parent/"static"/"index.html")
@app.get("/api/models")
async def models():
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False) as c:
            r=await c.get(BASE+"/models"); r.raise_for_status()
            return {"models":[x["id"] for x in r.json()["data"] if "embed" not in x["id"].lower()],"base_url":BASE}
    except Exception as e: raise HTTPException(502,f"Cannot reach LM Studio. Keep its API server running. Docker uses host.docker.internal:1234, not localhost. Details: {e}")
@app.get("/api/cases")
def cases():
    ROOT.mkdir(parents=True,exist_ok=True)
    result=[]
    for p in ROOT.glob("*/case.json"):
        try:
            c=json.loads(p.read_text()); result.append({k:c.get(k) for k in ("id","title","status","created")})
        except (ValueError,OSError): continue
    return sorted(result,key=lambda x:x["created"],reverse=True)
@app.post("/api/cases")
async def create(description: str=Form(...), flag_format: str=Form("flag{...}"), files: list[UploadFile]=File(default=[])):
    if not description.strip() or len(description)>16000: raise HTTPException(400,"Enter a description of up to 16000 characters")
    if len(files)>20: raise HTTPException(400,"Maximum 20 files per case")
    cid=uuid.uuid4().hex; folder=case_path(cid); work=folder/"work"; work.mkdir(parents=True)
    names=[]; total=0
    try:
        for f in files:
            name=re.sub(r"[^a-zA-Z0-9_.-]","_",Path(f.filename or "file").name).strip(".")[:120] or "file"
            if name in names: name=uuid.uuid4().hex[:8]+"_"+name
            names.append(name)
            with (work/name).open("wb") as out:
                while chunk:=await f.read(1024*1024):
                    total+=len(chunk)
                    if total>MAX_UPLOAD_BYTES: raise HTTPException(413,"Uploads exceed 1 GB (1,024 MB)")
                    out.write(chunk)
        case={"id":cid,"title":description.strip().splitlines()[0][:70],"description":description,"flag_format":flag_format[:200],"files":names,"created":time.time(),"status":"ready","events":[]}
        save_case(case); return case
    except Exception:
        import shutil
        shutil.rmtree(folder,ignore_errors=True); raise
@app.get("/api/cases/{cid}")
def case(cid: str): return read_case(cid)
class Config(BaseModel):
    models: dict[str,str]=Field(default_factory=lambda:{r:"qwen3-4b" for r in ROLES})
    steps: int=Field(default=3,ge=1,le=8)
    max_tokens: int=Field(default=700,ge=128,le=2048)
@app.post("/api/cases/{cid}/solve")
async def solve(cid: str,config: Config):
    case=read_case(cid)
    if active.locked(): raise HTTPException(409,"Another case is running. Stop it or wait.")
    await active.acquire()
    case["status"]="running"; case["events"]=[]; case.pop("result",None); save_case(case)
    state={"stop":False}; jobs[cid]=state
    async def emit(event):
        event["time"]=time.time(); case["events"].append(event); save_case(case)
    async def task():
        try:
            result=await Engine(BASE,WORKER).solve(case,config.model_dump(),emit,lambda:state["stop"])
            case["result"]=result; case["status"]=result["status"]
        except Exception as e:
            case["status"]="error"; await emit({"kind":"error","text":str(e)})
        finally: save_case(case); jobs.pop(cid,None); active.release()
    state["task"]=asyncio.create_task(task()); return {"status":"started"}
@app.post("/api/cases/{cid}/stop")
def stop(cid: str):
    read_case(cid)
    if cid in jobs: jobs[cid]["stop"]=True
    return {"status":"Stop requested; current model/tool call may finish first."}
@app.get("/api/cases/{cid}/export")
def export(cid: str):
    read_case(cid); return FileResponse(case_path(cid)/"case.json",filename=f"0xsolver-{cid[:8]}.json")
@app.on_event("startup")
def recover():
    ROOT.mkdir(parents=True,exist_ok=True)
    for p in ROOT.glob("*/case.json"):
        try:
            c=json.loads(p.read_text())
            if c["status"]=="running": c["status"]="interrupted"; save_case(c)
        except (OSError,ValueError,KeyError): pass
