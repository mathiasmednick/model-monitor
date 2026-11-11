import importlib.machinery
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
import hub_core as c

class HubTests(unittest.TestCase):
    def setUp(self):
        work=c.HERE/'data'/'test-work'; work.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work)
        self.path=Path(self.temp.name); self.state_patch=patch.object(c,'STATE',self.path/'data'); self.state_patch.start()
        self.row={'id':'ollama:test','name':"test-model:latest",'runtime':'Ollama','source':'http://127.0.0.1:11434','size':2*c.GiB,'meta':{}}
        self.p=c.validate_profile(c.default_profile(self.row))
    def tearDown(self): self.state_patch.stop(); self.temp.cleanup()

    def test_local_only_no_redirect(self):
        self.assertEqual(c.local_url('localhost:8080'),'http://localhost:8080')
        for value in ['http://example.org','http://localhost@evil.org','http://localhost/path','https://localhost','http://127.0.0.1:bad']:
            with self.assertRaises(ValueError): c.local_url(value)
        with self.assertRaises(ValueError): c.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_profile_validation(self):
        for p in [dict(self.p,output=20000),dict(self.p,context=0),dict(self.p,parallel=0),dict(self.p,temperature='nan'),dict(self.p,top_p=2)]:
            with self.assertRaises(ValueError): c.validate_profile(p)

    def test_atomic_profile_save(self):
        path=self.path/'profiles.json'; c.save_json(path,self.p)
        self.assertEqual(c.read_json(path),self.p); self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_ps_quoting(self):
        value="C:\\Alice's project\\$(whoami)"
        self.assertEqual(c.ps_quote(value),"'C:\\Alice''s project\\$(whoami)'")

    def test_launch_preserves_user_config_and_uses_alias(self):
        script=c.make_launch_script(self.row,self.p,'http://127.0.0.1:10000','modelhub-test:latest',self.path)
        self.assertIn('OPENCODE_CONFIG_CONTENT',script); self.assertIn('/api/create',script)
        self.assertIn('num_ctx',script); self.assertIn('modelhub-test:latest',script)
        self.assertNotIn('Set-Content',script); self.assertNotIn('--restart',script)
        self.assertNotIn('Stop-Process',script)

    def test_strata_flags_and_restart_are_explicit(self):
        row=dict(self.row,runtime='Strata',family='qwen',tag='IQ3_S')
        command=c.strata_command(row,self.p)
        self.assertIn('--parallel',command); self.assertNotIn('--restart',command)
        self.assertIn('--restart',c.strata_command(row,self.p,True))

    def test_warning_advisor_does_not_stop_anything(self):
        mem={'available':c.GiB,'commit_free':2*c.GiB}
        with patch.object(c,'stop_process') as stop:
            notes=c.advice(mem,[],6,{'available':False})
            self.assertTrue(any('LOW SYSTEM RAM' in s for s in notes)); stop.assert_not_called()
        self.assertTrue(c.preflight(self.row,self.p,mem))

    def test_gguf_reads_dimensions_without_weight_allocation(self):
        path=self.path/'test.gguf'
        def string(s):
            b=s.encode(); return struct.pack('<Q',len(b))+b
        data=b'GGUF'+struct.pack('<IQQ',3,1,1)
        data+=string('general.name')+struct.pack('<I',8)+string('Test')
        data+=string('weights')+struct.pack('<IQQIQ',2,12,20,0,0)
        path.write_bytes(data)
        result=c.read_gguf(path)
        self.assertEqual(result['tensor_parameters'],240); self.assertEqual(result['general.name'],'Test')

    def test_split_model_grouping(self):
        paths=[self.path/f'model-{i:05}-of-00002.gguf' for i in (1,2)]
        for path in paths: path.touch()
        self.assertEqual(c.model_files(paths[0]),paths)

    def test_offline_ollama_inventory(self):
        store=self.path/'.ollama/models'; manifest=store/'manifests/registry.ollama.ai/library/test/latest'
        manifest.parent.mkdir(parents=True); (store/'blobs').mkdir()
        (store/'blobs/sha256-config').write_text(json.dumps({'model_type':'7B','file_type':'Q4_K_M'}))
        (store/'blobs/sha256-weights').write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,0))
        manifest.write_text(json.dumps({'config':{'digest':'sha256:config'},'layers':[{'mediaType':'application/vnd.ollama.image.model','digest':'sha256:weights','size':24}]}))
        with patch.object(c,'HOME',self.path),patch.object(c,'STRATA',self.path/'Strata'),patch.object(c,'api',side_effect=OSError('offline')),patch.dict(os.environ,{'OLLAMA_MODELS':str(store)}):
            rows,warnings=c.inventory('http://127.0.0.1:11434',[])
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['name'],'test:latest')
        self.assertEqual(rows[0]['parameters'],7e9); self.assertTrue(warnings)

    def test_generated_powershell_parses_without_execution(self):
        for row in [self.row,dict(self.row,runtime='Strata',family='qwen',tag='IQ3_S'),
                    dict(self.row,runtime='GGUF file',files=[str(self.path/'example.gguf')])]:
            profile=dict(self.p,llama_exe=sys.executable)
            script=c.make_launch_script(row,profile,'http://127.0.0.1:10000','modelhub-test',self.path)
            path=self.path/'launch.ps1';path.write_text(script,encoding='utf-8-sig')
            command="$tokens=$null; $errors=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile("+c.ps_quote(path)+",[ref]$tokens,[ref]$errors); if($errors.Count){$errors | ForEach-Object {$_.Message}; exit 1}"
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_native_process_identity_and_stop(self):
        exe=self.path/'llama-server-hub-test.exe'; shutil.copy2(sys.executable,exe)
        env=dict(os.environ,PYTHONHOME=sys.base_prefix,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''))
        proc=subprocess.Popen([str(exe),'-c','import time; time.sleep(45)'],env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(.2); row=next(r for r in c.process_inventory() if r['pid']==proc.pid)
            with self.assertRaises(RuntimeError): c.stop_process(dict(row,birth=row['birth']+1))
            self.assertIsNone(proc.poll()); c.stop_process(row); proc.wait(timeout=5)
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=5)

    def test_real_streaming_gateway_and_metrics_privacy(self):
        received=[]
        class Backend(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length']))); received.append(body)
                self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
                for data in [{'choices':[{'delta':{'content':'secret response'}}]},
                    {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':200,'completion_tokens':10},'timings':{'predicted_per_second':25}}]:
                    self.wfile.write(('data: '+json.dumps(data)+'\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Backend)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        gateway=c.Gateway(self.row,self.p,'http://127.0.0.1:'+str(server.server_port),'actual-model')
        try:
            base=gateway.start()
            body={'model':'client-model','messages':[{'role':'user','content':'secret prompt'}],'stream':True,'max_tokens':99999}
            with urlopen(Request(base+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=5) as r: result=r.read()
            self.assertIn(b'[DONE]',result)
            self.assertEqual(received[0]['model'],'actual-model'); self.assertEqual(received[0]['max_tokens'],self.p['output'])
            self.assertTrue(received[0]['stream_options']['include_usage'])
            deadline=time.time()+2
            while not gateway.records and time.time()<deadline: time.sleep(.01)
            record=gateway.records[-1]
            self.assertEqual(record['prompt_tokens'],200); self.assertEqual(record['generation_tps'],25)
            log=(c.STATE/'request_metrics.jsonl').read_text()
            self.assertNotIn('secret prompt',log); self.assertNotIn('secret response',log)
        finally: gateway.close(); server.shutdown(); server.server_close()

    def test_gui_construction_numeric_sort_and_selection(self):
        loader=importlib.machinery.SourceFileLoader('hub_gui',str(c.HERE/'model_monitor.pyw'))
        spec=importlib.util.spec_from_loader(loader.name,loader); gui=importlib.util.module_from_spec(spec); loader.exec_module(gui)
        root=gui.tk.Tk(); root.withdraw()
        try:
            app=gui.Hub(root)
            app.models={str(i):dict(self.row,id=str(i),name=n,parameters=p,quant='Q4',status='Installed') for i,n,p in [(1,'Zulu',7e9),(2,'Alpha',70e9),(3,'Unknown',None)]}
            app.sort_key='parameters'; app.render_models()
            self.assertEqual(app.model_tree.get_children(),('1','2','3'))
            app.model_tree.selection_set('1'); app.render_models(); self.assertEqual(app.model_tree.selection(),('1',))
        finally: app.closing=True; root.destroy()

if __name__=='__main__': unittest.main(verbosity=2)
