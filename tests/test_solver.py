import asyncio, base64, importlib, json, os, tempfile, unittest
from pathlib import Path
TMP=tempfile.TemporaryDirectory()
os.environ['DATA_ROOT']=TMP.name
from fastapi.testclient import TestClient
from app.main import app
from app.common import case_path
from app.engine import Engine, ROLES, bounded_messages, tool_context
from app.worker import execute

class TriageTests(unittest.TestCase):
    def test_binary_text_recovers_jpeg_bytes(self):
        from app.triage import decode_bits
        import io
        from PIL import Image
        image=io.BytesIO(); Image.new('RGB',(8,8),'white').save(image,format='JPEG')
        original=image.getvalue()
        with tempfile.TemporaryDirectory() as temp:
            src=Path(temp)/'unknown';dst=Path(temp)/'decoded'
            src.write_text('\n'.join(format(x,'08b') for x in original))
            self.assertEqual(decode_bits(src,dst),len(original))
            self.assertEqual(dst.read_bytes(),original)
    def test_invalid_bits_do_not_leave_output(self):
        from app.triage import decode_bits
        with tempfile.TemporaryDirectory() as temp:
            src=Path(temp)/'source';dst=Path(temp)/'decoded'
            for value in ('01010102','010'):
                src.write_text(value)
                with self.assertRaises(ValueError): decode_bits(src,dst)
                self.assertFalse(dst.exists())

class ContextTests(unittest.TestCase):
    def test_binary_output_and_history_are_bounded(self):
        huge={'output':'01'*6000, 'exit_code':0}
        snippet=json.loads(tool_context(huge))
        self.assertLessEqual(len(snippet['output']),900)
        self.assertEqual(len(huge['output']),12000)
        messages=[{'role':'system','content':'Use tools.'},{'role':'user','content':'Challenge '+('x'*15000)}]
        for i in range(8):
            messages.extend([{'role':'assistant','content':'Inspect','tool_calls':[{'id':str(i),'type':'function','function':{'name':'run_command','arguments':'{"command":"strings digits.bin"}'}}]}, {'role':'tool','tool_call_id':str(i),'content':json.dumps(huge)}])
        bounded=bounded_messages(messages)
        self.assertLessEqual(len(json.dumps(bounded,ensure_ascii=True).encode()),4000)
        ids={c['id'] for m in bounded for c in m.get('tool_calls',[])}
        self.assertTrue(all(m['tool_call_id'] in ids for m in bounded if m['role']=='tool'))

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
