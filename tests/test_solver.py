import asyncio, base64, importlib, json, os, tempfile, unittest
from pathlib import Path
TMP=tempfile.TemporaryDirectory()
os.environ['DATA_ROOT']=TMP.name
from fastapi.testclient import TestClient
from app.main import app
from app.common import case_path
from app.engine import Engine, ROLES
from app.worker import execute

class ExecutionTests(unittest.TestCase):
    def test_timeout_and_output_bound(self):
        r=execute('sleep 10',TMP.name,1)
        self.assertTrue(r['timed_out'])
        r=execute("python -c \"print('x'*20000)\"",TMP.name)
        self.assertTrue(r['truncated']); self.assertEqual(len(r['output']),12000)
    def test_path_validation(self):
        with self.assertRaises(ValueError): case_path('../escape')

class ApiTests(unittest.TestCase):
    def test_upload_export_and_duplicate_names(self):
        with TestClient(app) as c:
            r=c.post('/api/cases',data={'description':'Decode this','flag_format':'ctf{...}'},files=[('files',('../x.txt',b'one')),('files',('x.txt',b'two'))])
            self.assertEqual(r.status_code,200); case=r.json()
            self.assertEqual(len(set(case['files'])),2)
            self.assertEqual((case_path(case['id'])/'work'/case['files'][0]).read_bytes(),b'one')
            self.assertEqual(c.get('/api/cases/'+case['id']+'/export').json()['description'],'Decode this')
            self.assertIn(case['id'],[x['id'] for x in c.get('/api/cases').json()])
            self.assertEqual(c.get('/api/cases/nope').status_code,404)
    def test_upload_limit_boundary_and_cleanup(self):
        from unittest.mock import patch
        from app.main import MAX_UPLOAD_BYTES
        self.assertEqual(MAX_UPLOAD_BYTES, 1024**3)
        with TestClient(app) as c, patch('app.main.MAX_UPLOAD_BYTES',8):
            exact=c.post('/api/cases',data={'description':'boundary'},files=[('files',('a',b'1234')),('files',('b',b'5678'))])
            self.assertEqual(exact.status_code,200)
            before=set(Path(TMP.name).iterdir())
            too_big=c.post('/api/cases',data={'description':'overflow'},files=[('files',('a',b'1234')),('files',('b',b'56789'))])
            self.assertEqual(too_big.status_code,413)
            self.assertEqual(set(Path(TMP.name).iterdir()),before)
    def test_empty_challenge_rejected(self):
        with TestClient(app) as c: self.assertEqual(c.post('/api/cases',data={'description':' '}).status_code,400)

class FakeEngine(Engine):
    def __init__(self): super().__init__('unused','unused'); self.calls=0; self.histories=[]
    async def command(self,cid,args): return execute(args['command'],case_path(cid)/'work',args.get('timeout',25))
    async def completion(self,payload):
        self.histories.append(json.loads(json.dumps(payload['messages'])))
        last=payload['messages'][-1]
        if last['role']=='tool':
            evidence=json.loads(last['content'])
            return {'role':'assistant','content':'Evidence from executed decoder: '+evidence['output'].strip()}
        if 'tools' in payload:
            return {'role':'assistant','content':'I will execute the decoder.','tool_calls':[{'id':'decode','type':'function','function':{'name':'run_command','arguments':json.dumps({'command':"python -c \"import base64; print(base64.b64decode(open('encoded.txt').read()).decode())\""})}}]}
        return {'role':'assistant','content':'Summary'}

class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_decoding_and_handoffs(self):
        cid='a'*32; work=case_path(cid)/'work';work.mkdir(parents=True,exist_ok=True)
        (work/'encoded.txt').write_text(base64.b64encode(b'ctf{actual_execution}').decode())
        events=[]
        async def emit(e): events.append(e)
        engine=FakeEngine()
        result=await engine.solve({'id':cid,'description':'Decode the file','files':['encoded.txt'],'flag_format':'ctf{...}'},{'models':{r:'fake' for r in ROLES},'steps':3,'max_tokens':300},emit,lambda:False)
        outputs=[e['result']['output'] for e in events if e['kind']=='tool']
        self.assertTrue(any('ctf{actual_execution}' in o for o in outputs))
        self.assertEqual(len([e for e in events if e['kind']=='status']),4)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(any('Previous agents: Coordinator:' in m[-1].get('content','') for m in engine.histories if m[-1]['role']=='user'))
    async def test_invalid_tool_is_reported(self):
        cid='c'*32;(case_path(cid)/'work').mkdir(parents=True,exist_ok=True)
        class Broken(FakeEngine):
            async def completion(self,payload):
                if 'tools' in payload:
                    return {'content':'', 'tool_calls':[{'id':'bad','type':'function','function':{'name':'run_command','arguments':'invalid json'}}]}
                return {'content':'No evidence found.'}
        events=[]
        async def emit(e): events.append(e)
        await Broken().solve({'id':cid,'description':'x','files':[],'flag_format':'flag{...}'},{'models':{},'steps':1,'max_tokens':128},emit,lambda:False)
        self.assertTrue(any('error' in e.get('result',{}) for e in events))
    async def test_stop_before_agents(self):
        cid='b'*32;(case_path(cid)/'work').mkdir(parents=True,exist_ok=True)
        async def emit(e): pass
        result=await FakeEngine().solve({'id':cid,'description':'x','files':[],'flag_format':'flag{...}'},{'models':{},'steps':1,'max_tokens':128},emit,lambda:True)
        self.assertEqual(result['status'],'stopped')

if __name__=='__main__': unittest.main()
