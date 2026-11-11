import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import hub_core as core
import hub_chat as chat
import hub_help as helpdesk

class HelpTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'gguf:C:/private-user/secret-folder/model.gguf','name':'Test model','runtime':'GGUF file','status':'Installed','parameters':7e9,'size':4*core.GiB,'files':['C:/private-user/secret-folder/model.gguf'],'meta':{}}
        self.models={self.row['id']:self.row}
        self.snapshot={'sample_time':'now','memory_bytes':{'total':32*core.GiB,'available':15*core.GiB,'commit_free':50*core.GiB,'installed_physical':128*core.GiB,'not_os_usable':96*core.GiB},
            'graphics':[{'name':'AMD graphics','capacity':96*core.GiB}],'gpu_counters':{'available':False},'model_processes':[],
            'consumers':{'top_apps':[{'name':'chrome.exe','processes':10,'working_set':8*core.GiB,'private':10*core.GiB}],
                'top_processes':[],'accessible_processes':100,'inaccessible_processes':2,'accessible_working_sets_sum':17*core.GiB,
                'all_processes':[{'pid':123,'birth':456,'name':'chrome.exe','working_set':8*core.GiB,'private':10*core.GiB}]}}

    def test_actual_answer_names_consumer_and_memory_split(self):
        answer=helpdesk.findings(self.snapshot)
        for text in ('not low right now','15.0 GB','96.0 GB','Google Chrome','8.0 GB','likely reason','Closing apps will not get that part back'):
            self.assertIn(text,answer)
        self.assertNotIn('check which',answer.lower())

    def test_low_memory_is_distinct_from_large_hardware_reservation(self):
        self.assertNotIn('The big limit',helpdesk.findings(self.snapshot))
        self.assertIn('not evidence that the memory is broken or lost',helpdesk.findings(self.snapshot))
        self.snapshot['memory_bytes']['available']=core.GiB
        self.assertIn('running low',helpdesk.findings(self.snapshot))

    def test_low_commit_has_concrete_repair_without_lowering_graphics(self):
        self.snapshot['memory_bytes']['commit_free']=core.GiB
        answer=helpdesk.findings(self.snapshot)
        self.assertIn('not low right now',answer)
        self.assertIn('Repair allocation limit',answer)
        self.assertIn('keeping your graphics reservation',answer)

    def test_cannot_claim_bios_cause_without_installed_total(self):
        del self.snapshot['memory_bytes']['installed_physical'];del self.snapshot['memory_bytes']['not_os_usable']
        self.assertIn('cannot confirm how much is reserved',helpdesk.findings(self.snapshot))

    def test_fix_choices_have_no_side_effects(self):
        with patch.object(core,'stop_process') as stop,patch.object(core,'save_json') as save,patch.object(helpdesk,'close_browser_windows') as close:
            choices=helpdesk.build_fixes(self.snapshot,self.models,{}, {},[{'name':'test','base':'http://127.0.0.1:11434','vram':core.GiB}])
            self.assertEqual({p['kind'] for p in choices},{'unload','close_browser'})
            stop.assert_not_called();save.assert_not_called();close.assert_not_called()

    def test_selected_model_gets_a_lighter_settings_choice(self):
        self.snapshot['selected_model_id']=self.row['id']
        choices=helpdesk.build_fixes(self.snapshot,self.models,{}, {},[])
        p=next(p for p in choices if p['kind']=='profile')
        self.assertEqual(p['after']['context'],8192);self.assertEqual(p['after']['parallel'],1)
        self.assertIn('future',p['summary'].lower())

    def test_report_excludes_paths_and_prompt_text(self):
        report=helpdesk.help_report(self.snapshot,self.models,{},[{'model':'Test','prompt':'PRIVATE PROMPT','api_key':'SECRET KEY','output_tokens':1}])
        for private in ('private-user','secret-folder','PRIVATE PROMPT','SECRET KEY','all_processes'):
            self.assertNotIn(private,report)
        self.assertIn(helpdesk.model_ref(self.row['id']),report)

    def test_external_plan_is_validated_but_not_applied(self):
        plan={'changes':[{'model_ref':helpdesk.model_ref(self.row['id']),'settings':{'context':8192},'reason':'Less memory'}]}
        with patch.object(core,'save_json') as save:
            rows=helpdesk.parse_advice('```json\n'+json.dumps(plan)+'\n```',self.models,{})
            self.assertEqual(rows[0]['after']['context'],8192);save.assert_not_called()

    def test_external_plan_cannot_run_code_or_edit_files(self):
        ref=helpdesk.model_ref(self.row['id'])
        for plan in [{'changes':[{'model_ref':ref,'settings':{'llama_exe':'evil.exe'}}]},
            {'changes':[{'model_ref':'unknown','settings':{'context':8192}}]},
            {'changes':[{'model_ref':ref,'settings':{'context':8192},'command':'whoami'}]},
            {'changes':[{'model_ref':ref,'settings':{'context':8192}},{'model_ref':ref,'settings':{'context':4096}}]}]:
            with self.assertRaises(ValueError):helpdesk.parse_advice(json.dumps(plan),self.models,{})

    def test_browser_fix_refuses_other_apps(self):
        with self.assertRaises(ValueError):helpdesk.close_browser_windows([{'name':'opencode.exe','pid':123,'birth':456}])

    def test_browser_close_uses_normal_window_close_on_disposable_app(self):
        work=core.HERE/'data'/'test-work';work.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as temp:
            folder=Path(temp);exe=folder/'chrome.exe';shutil.copy2(sys.executable,exe);ready=folder/'ready'
            env=dict(os.environ,PYTHONHOME=sys.base_prefix,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''))
            program='import tkinter as t;from pathlib import Path;r=t.Tk();r.title("Model Monitor test window");r.after(100,lambda:Path('+repr(str(ready))+').touch());r.after(15000,r.destroy);r.mainloop()'
            proc=subprocess.Popen([str(exe),'-c',program],env=env,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                until=time.time()+5
                while not ready.exists() and time.time()<until:time.sleep(.05)
                self.assertTrue(ready.exists())
                h=core.native.K.OpenProcess(0x1000,False,proc.pid)
                try:born=core.native.birth(h)
                finally:core.native.K.CloseHandle(h)
                count=helpdesk.close_browser_windows([{'name':'chrome.exe','pid':proc.pid,'birth':born}])
                self.assertGreaterEqual(count,1);self.assertEqual(proc.wait(timeout=5),0)
            finally:
                if proc.poll() is None:proc.kill();proc.wait(timeout=5)

if __name__=='__main__':unittest.main(verbosity=2)
