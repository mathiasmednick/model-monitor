from copy import deepcopy
from pathlib import Path
import importlib.machinery
import importlib.util
import unittest
from unittest.mock import patch,MagicMock
import hub_core as core
import hub_display as display
import hub_repair as repair

class OptimizeTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'ollama:test','name':'test:latest','runtime':'Ollama','source':'http://127.0.0.1:11434','files':[],'meta':{}}
        self.p=core.validate_profile(core.default_profile(self.row));self.cancel=MagicMock();self.cancel.is_set.return_value=False
        self.mem={'available':64*core.GiB,'commit_free':64*core.GiB}

    def passed(self,choice,speed=20,comparable=True):
        return {'status':'passed','gpu_confirmed':True,'answers_passed':True,'choice':deepcopy(choice),
            'evidence':{'backend_used':'rocm_v7_1' if choice['backend']=='auto' else choice['backend']},
            'benchmark':{'comparable':comparable,'tokens':64,'job_tps':speed,'generation_tps':speed}},None

    def test_selects_measured_fastest_and_does_not_save_without_ui_approval(self):
        def run(row,choice,*args,**kwargs):return self.passed(choice,40 if choice['backend']=='vulkan' else 20)
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=run) as test,patch.object(repair,'save_recipe') as save:
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertEqual(result['choice']['backend'],'vulkan');self.assertEqual(test.call_count,4)
        self.assertTrue(test.call_args.kwargs['context_probe'])
        self.assertEqual(result['choice']['profile']['parallel'],1);self.assertEqual(result['choice']['profile']['context'],65536)
        self.assertEqual(len(result['comparison']),4);save.assert_not_called()

    def test_faster_32k_beats_64k_even_after_50_is_exceeded(self):
        def run(row,choice,*a,**kw):
            return self.passed(choice,85 if choice['profile']['context']==32768 else 55)
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=run) as calls:
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertEqual(result['choice']['profile']['context'],32768)
        self.assertEqual(result['performance_goal']['measured_writing_tps'],85)
        self.assertEqual(result['performance_goal']['target_tps'],50)
        self.assertTrue(result['performance_goal']['target_met'])
        self.assertEqual({c.args[1]['profile']['context'] for c in calls.call_args_list},{32768,65536})

    def test_tiny_faster_result_cannot_replace_eligible_long_context(self):
        def run(row,choice,*a,**kw):
            return self.passed(choice,100 if choice['profile']['context']<32768 else 28)
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=run):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertTrue(result['performance_goal']['context_goal_met'])
        self.assertFalse(result['performance_goal']['target_met'])
        self.assertEqual(result['choice']['profile']['context'],65536)

    def test_compares_fallback_routes_at_same_context(self):
        calls=[]
        def run(row,choice,*args,**kwargs):
            calls.append((choice['backend'],choice['profile']['context']))
            if choice['profile']['context']>8192:raise ValueError('does not fit')
            return self.passed(choice,30 if choice['backend']=='vulkan' else 10)
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=run):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertEqual(result['choice']['profile']['context'],8192)
        self.assertIn(('vulkan',8192),calls);self.assertEqual(result['choice']['backend'],'vulkan')

    def test_model_context_limit_is_respected(self):
        row=dict(self.row,meta={'test.context_length':4096})
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=lambda row,choice,*a,**kw:self.passed(choice)):
            result=repair.optimize(row,self.p,self.cancel,lambda _:None)
        self.assertEqual(result['choice']['profile']['context'],4096)

    def test_missing_speed_does_not_claim_fastest(self):
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=lambda row,choice,*a,**kw:self.passed(choice,comparable=False)):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertIn('Comparable speed readings were not available',result['recommendation'])

    def test_32k_goal_is_supported_and_preferred_over_smaller_contexts(self):
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=lambda row,choice,*a,**kw:self.passed(choice,30)) as run:
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None,goal_context=32768)
        self.assertEqual(result['choice']['profile']['context'],32768)
        self.assertTrue(all(c.args[1]['profile']['context']==32768 for c in run.call_args_list))

    def test_slow_pass_is_not_claimed_to_meet_speed_goal(self):
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=lambda row,choice,*a,**kw:self.passed(choice,12)):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertFalse(result['performance_goal']['minimum_met']);self.assertEqual(result['performance_goal']['minimum_tps'],25)

    def test_fast_tiny_context_does_not_meet_long_chat_goal(self):
        def run(row,choice,*a,**kw):
            return self.passed(choice,50 if choice['profile']['context']<32768 else 10)
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=run):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertTrue(result['performance_goal']['minimum_met']);self.assertFalse(result['performance_goal']['context_goal_met'])

    def test_large_context_requires_actual_input_token_evidence(self):
        p=dict(self.p,context=65536);choice={'backend':'existing','profile':p};row=dict(self.row,runtime='GGUF file')
        session=MagicMock();session.choice=choice;session.row=row
        session.log_text.return_value='offloaded 48/48 layers to GPU\nn_ctx_per_seq = 65536'
        answers=[{'text':'READY','tokens':1,'finished':True},{'text':'4','tokens':1,'finished':True},
            {'text':'READY','tokens':1,'finished':True,'prompt_tokens':41000,'seconds':10}]
        with patch.object(repair,'Session',return_value=session),patch.object(repair,'answer',side_effect=answers),patch.object(repair,'fingerprint',return_value='fixture'):
            result,_=repair.run_choice(row,choice,self.cancel,lambda _:None,context_probe=True)
        self.assertEqual(result['context_probe']['input_tokens'],41000);session.stop.assert_called_once()
        session.reset_mock();answers[-1]=dict(answers[-1],prompt_tokens=100)
        with patch.object(repair,'Session',return_value=session),patch.object(repair,'answer',side_effect=answers),patch.object(repair,'fingerprint',return_value='fixture'):
            with self.assertRaises(repair.TrialFailure):repair.run_choice(row,choice,self.cancel,lambda _:None,context_probe=True)

    def test_failed_optimization_cannot_supply_a_passing_default(self):
        with patch.object(core,'system_memory',return_value=self.mem),patch.object(repair,'run_choice',side_effect=ValueError('failed')):
            result=repair.optimize(self.row,self.p,self.cancel,lambda _:None)
        self.assertEqual(result['status'],'failed');self.assertNotIn('choice',result)

    def test_backend_library_loaded_is_not_model_device_proof(self):
        self.assertIsNone(repair.used_backend('loaded Vulkan backend\nloaded ROCm backend'))
        self.assertEqual(repair.used_backend('using device ROCm0'),'rocm_v7_1')

    def test_header_is_gpu_first_without_inventing_free_vram(self):
        hardware=[{'name':'AMD Radeon 8060S','capacity':96*core.GiB}]
        memory={'total':32*core.GiB,'available':16*core.GiB}
        model={'base':'http://localhost:11434','name':'test','vram':48*core.GiB}
        primary,secondary=display.memory_header(hardware,memory,[model,model],{},128*core.GiB)
        self.assertTrue(primary.startswith('Graphics memory setting: 96.0 GB'))
        self.assertIn('48.0 GB',primary);self.assertNotIn('96.0 GB free',primary)
        self.assertIn('Free GPU memory: not verified',primary)
        self.assertIn('Unified RAM installed: 128.0 GB',secondary)
        self.assertIn('16.0 GB free of 32.0 GB usable',secondary)

    def test_unknown_gpu_use_is_not_shown_as_zero(self):
        primary,secondary=display.memory_header([{'name':'AMD Radeon 8060S','capacity':96*core.GiB}],{},[],{})
        self.assertIn('model GPU use: not reported',primary)

    def test_low_commit_warning_does_not_claim_low_physical_ram(self):
        text=display.memory_warning({'available':12*core.GiB,'commit_free':core.GiB})
        self.assertIn('LOW ALLOCATION ROOM',text)
        self.assertNotIn('LOW WINDOWS RAM',text)
        self.assertEqual(display.memory_warning({'available':12*core.GiB,'commit_free':40*core.GiB}),'')

    def test_chat_has_large_resizable_composer_and_no_intro_dump(self):
        loader=importlib.machinery.SourceFileLoader('opt_gui',str(core.HERE/'model_monitor.pyw'))
        spec=importlib.util.spec_from_loader(loader.name,loader);gui=importlib.util.module_from_spec(spec);loader.exec_module(gui)
        root=gui.tk.Tk();root.withdraw()
        try:
            app=gui.Hub(root)
            self.assertGreaterEqual(int(app.chat_input.cget('height')),6)
            self.assertEqual(len(app.chat_split.panes()),2)
            self.assertEqual(app.chat_text.get('1.0','end').strip(),'')
            self.assertEqual(len(app.navigation),5)
            self.assertEqual(str(app.project_picker.cget('textvariable')),str(app.project))
            self.assertEqual(str(app.project_picker.cget('state')),'readonly')
            self.assertEqual(app.settings_window.state(),'withdrawn')
            self.assertIn('As fast as possible',app.speed_caption.get())
            self.assertFalse(app.proposal_panel.winfo_manager())
            app.speed_min.set('25');app.speed_target.set('75')
            with patch.object(core,'save_json') as saved:
                app.persist_settings()
            self.assertEqual(saved.call_args.args[1]['target_tps'],75)
            app.speed_target.set('nan')
            with self.assertRaises(ValueError):app.optimization_preferences()
        finally:app.closing=True;root.destroy()

if __name__=='__main__':unittest.main(verbosity=2)
