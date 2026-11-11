import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import hub_core as core
import hub_chat as chat

class ChatTests(unittest.TestCase):
    def setUp(self):
        work=core.HERE/'data'/'test-work';work.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work);self.path=Path(self.temp.name)
        self.row={'id':'ollama:test','name':'test:latest','runtime':'Ollama','source':'http://127.0.0.1:11434','size':core.GiB,'status':'Installed','meta':{}}
        self.models={self.row['id']:self.row};self.profiles={}
        self.snapshot={'sample_time':'now','memory_bytes':{'total':32*core.GiB,'available':15*core.GiB,'commit_free':60*core.GiB,'installed_physical':128*core.GiB,'not_os_usable':96*core.GiB,'system_process_count':300},
            'graphics':[],'gpu_counters':{'available':False},'consumers':{'top_apps':[],'top_processes':[],'accessible_processes':3,'inaccessible_processes':1},'model_processes':[],'models':[]}
        self.target={'runtime':'Ollama','model':'test:latest','base':'http://127.0.0.1:11434'}
    def tearDown(self):self.temp.cleanup()

    def test_ram_explanation_separates_reserved_and_available(self):
        text=chat.memory_explanation(self.snapshot)
        for phrase in ('15.00 GiB','32.00 GiB','128.00 GiB','96.00 GiB','LIMITED PROCESS VISIBILITY','No settings or processes were changed'):
            self.assertIn(phrase,text)

    def test_profile_proposal_stages_without_applying(self):
        with patch.object(core,'save_json') as save,patch.object(core,'stop_process') as stop:
            p=chat.profile_proposal(self.row['id'],{'context':8192},'Reduce cache',self.models,self.profiles)
            self.assertEqual(p['after']['context'],8192);self.assertEqual(self.profiles,{})
            save.assert_not_called();stop.assert_not_called()

    def test_profile_proposal_rejects_arbitrary_paths_and_invalid_values(self):
        for changes in ({'llama_exe':'evil.exe'},{'runtime_base':'https://api.openai.com'},{'context':1},{'output':999999},{'gpu_layers':0},{'kv':'q4_0'}):
            with self.assertRaises(ValueError):chat.profile_proposal(self.row['id'],changes,'',self.models,self.profiles)

    def test_stop_proposals_only_allow_identified_model_processes(self):
        processes={'x':{'pid':123,'kind':'OpenCode','birth':55,'name':'opencode.exe'}}
        with self.assertRaises(ValueError):chat.stop_proposal(123,'stop',processes)
        processes['x']['kind']='Strata'
        with patch.object(core,'stop_process') as stop:
            p=chat.stop_proposal(123,'unused',processes);self.assertEqual(p['process']['birth'],55);stop.assert_not_called()

    def test_ollama_cloud_model_is_rejected_before_generation(self):
        def api(base,path,body=None,**kwargs):
            if path=='/api/ps':return {'models':[{'name':'test:latest'}]}
            if path=='/api/show':return {'model_info':{},'remote_host':'https://ollama.com','remote_model':'test'}
            raise AssertionError('Generation must not be called')
        with patch.object(core,'api',side_effect=api):
            with self.assertRaises(ValueError):chat.chat_response(self.target,[])

    def test_unloaded_model_is_not_woken(self):
        with patch.object(core,'api',return_value={'models':[]}) as api:
            with self.assertRaises(ValueError):chat.chat_response(self.target,[])
            self.assertEqual(api.call_count,1)

    def test_native_chat_is_local_and_does_not_change_context(self):
        calls=[]
        def api(base,path,body=None,**kwargs):
            calls.append((base,path,body))
            if path=='/api/ps':return {'models':[{'name':'test:latest'}]}
            if path=='/api/show':return {'model_info':{'general.architecture':'test'}}
            return {'message':{'role':'assistant','content':'hello'}}
        with patch.object(core,'api',side_effect=api):self.assertEqual(chat.chat_response(self.target,[])['content'],'hello')
        self.assertTrue(all(base.startswith('http://127.0.0.1:') for base,_,_ in calls))
        self.assertNotIn('num_ctx',calls[-1][2]['options']);self.assertEqual(calls[-1][1],'/api/chat')

    def test_tool_turn_only_stages_profile_edit(self):
        responses=[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'propose_profile_change','arguments':{'model_id':self.row['id'],'changes':{'context':8192},'reason':'Reduce memory'}}}]},
            {'role':'assistant','content':'Review the proposed context change.'}]
        with patch.object(chat,'chat_response',side_effect=responses),patch.object(core,'save_json') as save,patch.object(core,'stop_process') as stop:
            answer,actions=chat.run_turn(self.target,[],'reduce context',self.snapshot,self.models,self.profiles,{},[])
            self.assertEqual(len(actions),1);self.assertIn('Review',answer);save.assert_not_called();stop.assert_not_called()

    def test_cancelled_chat_has_no_request(self):
        cancel=threading.Event();cancel.set()
        with patch.object(core,'api') as api:
            with self.assertRaises(InterruptedError):chat.chat_response(self.target,[],cancel=cancel)
            api.assert_not_called()

    def test_gui_apply_profile_requires_review_and_preserves_running_work(self):
        loader=importlib.machinery.SourceFileLoader('chat_gui',str(core.HERE/'model_monitor.pyw'))
        spec=importlib.util.spec_from_loader(loader.name,loader);gui=importlib.util.module_from_spec(spec);loader.exec_module(gui)
        root=gui.tk.Tk();root.withdraw()
        try:
            app=gui.Hub(root);app.models=self.models
            proposal=chat.profile_proposal(self.row['id'],{'context':8192},'Reduce memory',app.models,app.profiles)
            proposal['state']='Pending';app.chat_proposals[proposal['id']]=proposal
            app.proposal_tree.insert('','end',iid=proposal['id'],values=(proposal['name'],proposal['summary'],'Pending'));app.proposal_tree.selection_set(proposal['id'])
            with patch.object(gui.messagebox,'askyesno',return_value=False),patch.object(core,'save_json') as save:
                app.apply_proposal();save.assert_not_called();self.assertEqual(proposal['state'],'Pending')
            with patch.object(gui.messagebox,'askyesno',return_value=True),patch.object(core,'STATE',self.path),patch.object(core,'stop_process') as stop:
                app.apply_proposal();self.assertEqual(app.profiles[self.row['id']]['context'],8192)
                self.assertEqual(proposal['state'],'Applied');stop.assert_not_called()
        finally:app.closing=True;root.destroy()

if __name__=='__main__':unittest.main(verbosity=2)
