import importlib.machinery
import importlib.util
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
import hub_core as core

loader=importlib.machinery.SourceFileLoader('restart_gui',str(core.HERE/'model_monitor.pyw'))
spec=importlib.util.spec_from_loader(loader.name,loader)
gui=importlib.util.module_from_spec(spec);loader.exec_module(gui)

class RestartTests(unittest.TestCase):
    def setUp(self):
        self.row={'id':'ollama:test','name':'test','runtime':'Ollama'}
        self.p=dict(core.default_profile(self.row),context=32768)
        self.recipe={'choice':{'backend':'vulkan','profile':self.p},'fingerprint':'old','status':'passed'}
        self.app=SimpleNamespace(repair_busy=False,managed_sessions=[],project=MagicMock(),status=MagicMock(),events=queue.Queue())
        self.app.project.get.return_value=str(core.HERE)
        self.app.remember_project=MagicMock()

    def run_start(self,accepted=True,failure=None):
        self.result={'status':'passed','choice':self.recipe['choice']}
        with patch.object(gui.repair,'recipe_state',return_value='Changed — test again'),patch.object(gui.repair,'recipe_changes',return_value=['Identity differs']),patch.object(gui.messagebox,'askyesno',return_value=accepted) as ask,patch.object(gui.repair,'run_choice',return_value=(self.result,MagicMock()),side_effect=failure) as run,patch.object(gui.threading,'Thread',side_effect=lambda target,**kw:SimpleNamespace(start=target)):
            gui.Hub.launch_tested(self.app,self.row,self.p,self.recipe)
        return ask,run

    def test_changed_record_offers_recheck_and_runs_required_validation(self):
        ask,run=self.run_start()
        self.assertEqual(ask.call_args.args[0],'Recheck and start?')
        self.assertTrue(run.call_args.kwargs['benchmark']);self.assertTrue(run.call_args.kwargs['context_probe'])
        self.assertTrue(run.call_args.kwargs['keep'])
        event,data=self.app.events.get_nowait()
        self.assertEqual(event,'verified_started');self.assertTrue(data[1]['refresh_saved_setup'])

    def test_declining_recheck_never_loads_model(self):
        _,run=self.run_start(accepted=False)
        run.assert_not_called();self.assertTrue(self.app.events.empty())

    def test_failed_recheck_never_reports_success(self):
        self.run_start(failure=ValueError('GPU unavailable'))
        event,data=self.app.events.get_nowait()
        self.assertEqual(event,'verified_start_error');self.assertIn('GPU unavailable',data[1])

    def test_stopping_server_does_not_invalidate_saved_setup(self):
        self.app.events.put(('managed_alert',({'row':self.row},'The tested model server stopped.')))
        self.app.closing=True;self.app.choose_model=MagicMock()
        with patch.object(core,'save_json') as save:
            gui.Hub.poll(self.app)
        save.assert_not_called();self.app.choose_model.assert_called_once()

    def test_declining_slow_recheck_preserves_saved_recipe(self):
        self.app.closing=False;self.app.speed_min=MagicMock();self.app.speed_min.get.return_value='25'
        session=MagicMock()
        result={'refresh_saved_setup':True,'benchmark':{'comparable':True,'generation_tps':12}}
        with patch.object(gui.messagebox,'askyesno',return_value=False),patch.object(gui.repair,'save_recipe') as save:
            gui.Hub.finish_verified_start(self.app,(self.row,result,session,str(core.HERE)))
        session.stop.assert_called_once();save.assert_not_called()

if __name__=='__main__':unittest.main()
