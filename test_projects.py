from pathlib import Path
import tempfile
import unittest
import hub_core as core
import hub_projects as projects
import hub_terminal as terminal

class ProjectTests(unittest.TestCase):
    def test_recent_folders_are_valid_unique_and_bounded(self):
        work=core.STATE/'test-work';work.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as temp:
            root=Path(temp);folders=[root/str(i) for i in range(10)]
            for folder in folders:folder.mkdir()
            selected,recent=projects.remember(folders[2],[str(folders[2]),str(root/'missing'),*[str(x) for x in folders]])
            self.assertEqual(selected,recent[0]);self.assertEqual(len(recent),8)
            self.assertEqual(len(set(recent)),8);self.assertNotIn(str(root/'missing'),recent)
            with self.assertRaises(ValueError):projects.remember(root/'missing')

    def test_project_and_readable_model_name_are_in_launch_configuration(self):
        row={'name':'Qwen coder','runtime':'Ollama','source':'http://127.0.0.1:11434'};p=core.default_profile(row)
        config=terminal.config('internal-alias','http://127.0.0.1:1234',p,core.HERE,display_name=row['name'])
        model=config['providers']['modelhub']['models']['internal-alias']
        self.assertEqual(model['name'],'Qwen coder');self.assertEqual(model['modelID'],'internal-alias')
        script=core.make_launch_script(row,p,'http://127.0.0.1:1234','internal-alias',core.HERE,managed=True,simple=False)
        self.assertIn('Set-Location -LiteralPath '+core.ps_quote(core.HERE),script)
        self.assertIn('WindowTitle',script);self.assertIn('Project: '+str(core.HERE),script)
        self.assertIn('Model: Qwen coder',script)

if __name__=='__main__':unittest.main()
