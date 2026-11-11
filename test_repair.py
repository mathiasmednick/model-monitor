from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
import tempfile
import threading
import os
import socket
import ctypes as C
from ctypes import wintypes as W
import struct
import json
import unittest
from unittest.mock import MagicMock,patch
import hub_core as core
import hub_repair as r

class RepairTests(unittest.TestCase):
    def setUp(self):
        work=core.HERE/'data/test-work';work.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work);self.path=Path(self.temp.name)
        self.state=patch.object(core,'STATE',self.path);self.state.start()
        self.row={'id':'ollama:test','name':'test:latest','runtime':'Ollama','source':'http://127.0.0.1:11434','files':[]}
        self.p=core.validate_profile(core.default_profile(self.row));self.choice={'backend':'auto','profile':self.p}
        self.session=SimpleNamespace(row=self.row,choice=self.choice,base='http://127.0.0.1:12345',model='test:latest',log_text=lambda:'loaded ROCm backend')
        self.responses=[{'text':'READY','tokens':1,'finished':True},{'text':'4','tokens':1,'finished':True}]
    def tearDown(self):self.state.stop();self.temp.cleanup()

    def test_requires_finished_answers_and_gpu_memory(self):
        with patch.object(core,'api',return_value={'models':[{'name':'test:latest','size':100,'size_vram':100,'context_length':self.p['context']}]}) as api:
            self.assertEqual(r.assess(self.session,self.responses)['graphics_bytes'],100)
            with self.assertRaises(ValueError):r.assess(self.session,[{'text':'','tokens':0}])
            api.return_value={'models':[{'name':'test:latest','size':100,'size_vram':0,'context_length':self.p['context']}]}
            with self.assertRaises(ValueError):r.assess(self.session,self.responses)

    def test_partial_gpu_or_wrong_context_cannot_pass(self):
        for vram,context in [(50,self.p['context']),(100,4096),(100,None)]:
            with patch.object(core,'api',return_value={'models':[{'name':'test:latest','size':100,'size_vram':vram,'context_length':context}]}):
                with self.assertRaises(ValueError):r.assess(self.session,self.responses)

    def test_wrong_simple_answer_cannot_pass(self):
        with self.assertRaises(ValueError):r.assess(self.session,[{'text':'READY','tokens':1},{'text':'five','tokens':1}])

    def test_requested_backend_must_be_confirmed(self):
        self.session.choice={'backend':'vulkan','profile':self.p}
        with patch.object(core,'api',return_value={'models':[{'name':'test:latest','size':100,'size_vram':100,'context_length':self.p['context']}]}):
            with self.assertRaises(ValueError):r.assess(self.session,self.responses)
            self.session.log_text=lambda:'load_backend: loaded Vulkan backend\nusing device Vulkan0'
            self.assertTrue(r.assess(self.session,self.responses))

    def test_strata_and_gguf_need_fresh_gpu_proof(self):
        for runtime in ('Strata','GGUF file'):
            self.session.row=dict(self.row,runtime=runtime);self.session.log_text=lambda:''
            with self.assertRaises(ValueError):r.assess(self.session,self.responses)
        self.session.log_text=lambda:'offloaded 48/48 layers to GPU\nn_ctx_per_seq = '+str(self.p['context'])
        self.assertEqual(r.assess(self.session,self.responses)['offloaded_layers'],48)

    def test_failed_result_cannot_be_saved(self):
        with self.assertRaises(ValueError):r.save_recipe(self.row,{'status':'failed'})
        self.assertFalse((self.path/'verified_recipes.json').exists())

    def test_save_and_undo_restore_previous_recipe_and_profile(self):
        old={'context':12345};first={'status':'passed','gpu_confirmed':True,'answers_passed':True,'choice':self.choice,'fingerprint':'one'}
        second=dict(first,fingerprint='two')
        r.save_recipe(self.row,first,old);r.save_recipe(self.row,second,self.p)
        self.assertEqual(r.undo_recipe(self.row['id']),self.p)
        self.assertEqual(core.read_json(self.path/'verified_recipes.json')[self.row['id']]['fingerprint'],'one')
        self.assertEqual(r.undo_recipe(self.row['id']),old)
        self.assertNotIn(self.row['id'],core.read_json(self.path/'verified_recipes.json'))

    def test_changed_fingerprint_demands_retest(self):
        recipe={'status':'passed','fingerprint':'old'}
        with patch.object(r,'fingerprint',return_value='new'):self.assertIn('test again',r.recipe_state(self.row,self.p,recipe))

    def test_legacy_change_reason_does_not_invent_a_driver_change(self):
        recipe={'status':'passed','choice':{'profile':self.p},'fingerprint':'old'}
        reasons=r.recipe_changes(self.row,self.p,recipe)
        self.assertIn('cannot identify',reasons[0])

    def test_change_details_identify_context_and_driver(self):
        recipe={'status':'passed','choice':{'profile':self.p},'fingerprint_details':{'drivers':['old']}}
        with patch.object(r,'fingerprint_details',return_value={'drivers':['new']}):
            reasons=r.recipe_changes(self.row,dict(self.p,context=32768),recipe)
        self.assertTrue(any('context:' in x for x in reasons))
        self.assertIn('Graphics driver changed or could not be read the same way.',reasons)

    def test_sampling_tweaks_do_not_hide_memory_setting_changes(self):
        exe=self.path/'ollama.exe';exe.write_bytes(b'fixture')
        with patch.object(r.shutil,'which',return_value=str(exe)),patch.object(core,'gpu_hardware',return_value=[]):
            original=r.fingerprint(self.row,self.p)
            self.assertEqual(original,r.fingerprint(self.row,dict(self.p,temperature=.8,top_p=.8,output=2048)))
            self.assertNotEqual(original,r.fingerprint(self.row,dict(self.p,context=32768)))

    def test_trials_stop_at_first_pass_and_do_not_save_it(self):
        cancel=MagicMock();cancel.is_set.return_value=False
        passed={'status':'passed','gpu_confirmed':True,'answers_passed':True}
        with patch.object(core,'system_memory',return_value={'available':64*core.GiB,'commit_free':64*core.GiB}),patch.object(r,'run_choice',side_effect=[ValueError('failed'),(passed,None)]) as run,patch.object(r,'save_recipe') as save:
            result=r.run_trials(self.row,self.p,cancel,lambda text:None)
            self.assertEqual(result['status'],'passed');self.assertEqual(run.call_count,2);save.assert_not_called()

    def test_cancel_starts_no_server(self):
        cancel=threading.Event();cancel.set()
        with patch.object(r,'Session') as session:
            with self.assertRaises(r.Cancelled):r.run_trials(self.row,self.p,cancel,lambda text:None)
            session.assert_not_called()

    def test_cleanup_targets_only_owned_process_tree(self):
        session=r.Session(self.row,self.choice);session.process=MagicMock(pid=55555);session.process.poll.return_value=None
        with patch.object(r.subprocess,'run') as run:
            session.stop()
            self.assertEqual(run.call_args.args[0],['taskkill.exe','/PID','55555','/T','/F'])

    def test_managed_launch_does_not_create_another_server(self):
        p=dict(self.p,port=12345,runtime_base=self.session.base)
        script=core.make_launch_script(dict(self.row,source=self.session.base),p,'http://127.0.0.1:12346','test:latest',self.path,managed=True)
        self.assertNotIn('Start-Process',script);self.assertNotIn('/api/create',script)
        self.assertIn('opencode --standalone',script);self.assertNotIn('opencode --model',script)

    def test_listener_must_belong_to_test_process(self):
        native=C.WinDLL('iphlpapi').GetExtendedTcpTable;native.argtypes=[C.c_void_p,C.POINTER(W.DWORD),W.BOOL,W.ULONG,C.c_int,W.ULONG]
        size=W.DWORD()
        if native(None,C.byref(size),False,2,5,0)==5:self.skipTest('This execution sandbox denies the Windows TCP-owner API; the desktop app must verify it.')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            port=listener.getsockname()[1]
            self.assertTrue(r.port_owned_by(port,os.getpid()))
            self.assertFalse(r.port_owned_by(port,99999999))

    def test_owner_table_parser_rejects_another_process(self):
        raw=struct.pack('<7I',1,2,0,socket.htons(12345),0,0,55)
        def native(buf,size,*args):
            C.cast(size,C.POINTER(W.DWORD)).contents.value=len(raw)
            if buf is None:return 122
            C.memmove(buf,raw,len(raw));return 0
        function=MagicMock(side_effect=native)
        with patch.object(r.C,'WinDLL',return_value=SimpleNamespace(GetExtendedTcpTable=function)):
            self.assertTrue(r.port_owned_by(12345,55));self.assertFalse(r.port_owned_by(12345,56))

    def test_complete_local_api_trial_saves_nothing_and_cleans_up(self):
        instances=[];test=self
        class FakeSession(r.Session):
            def start(self):
                owner=self;owner.alias='';owner.finished=0;self.log=test.path/'runtime.log';self.log.write_text('loaded Vulkan backend\nusing device Vulkan0')
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_GET(self):self.reply()
                    def do_POST(self):self.reply()
                    def reply(self):
                        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
                        if self.path=='/api/tags':data={'models':[{'name':test.row['name']}]}
                        elif self.path=='/api/show':data={'model_info':{'general.architecture':'test'}}
                        elif self.path=='/api/create':owner.alias=body['model'];data={'status':'success'}
                        elif self.path=='/api/generate':
                            owner.finished+=1;data={'response':'READY' if owner.finished==1 else '4','eval_count':1,'done':True,'done_reason':'stop'}
                        elif self.path=='/api/ps':data={'models':[{'name':owner.alias,'size':100,'size_vram':100,'context_length':test.p['context']}]}
                        else:data={}
                        payload=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.base='http://127.0.0.1:'+str(self.server.server_port)
                threading.Thread(target=self.server.serve_forever,daemon=True).start();instances.append(self)
            def ready(self,*args):pass  # TCP-owner parsing is independently tested above.
            def stop(self):self.server.shutdown();self.server.server_close();self.stopped=True
        choice={'backend':'vulkan','profile':self.p}
        with patch.object(r,'Session',FakeSession),patch.object(r,'fingerprint',return_value='fixture'):
            result,session=r.run_choice(self.row,choice,threading.Event(),lambda _:None)
        self.assertEqual(result['status'],'passed');self.assertIsNone(session)
        self.assertTrue(instances[0].stopped);self.assertEqual(instances[0].finished,2)
        self.assertFalse((test.path/'verified_recipes.json').exists())

if __name__=='__main__':unittest.main(verbosity=2)
