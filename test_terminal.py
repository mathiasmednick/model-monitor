import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import build_opener,ProxyHandler,Request
from urllib.parse import urlencode
from urllib.error import HTTPError
import hub_core as core
import hub_terminal as terminal

class TerminalTests(unittest.TestCase):
    def setUp(self):
        work=core.HERE/'data/test-work';work.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work);self.path=Path(self.temp.name)
        self.p=core.validate_profile(core.default_profile({'runtime':'Ollama'}));self.p['context']=65536
    def tearDown(self):self.temp.cleanup()

    def test_v2_config_uses_local_model_and_disables_cluster_agents(self):
        config=terminal.config('test','http://127.0.0.1:12345',self.p,self.path)
        self.assertEqual(config['providers']['modelhub']['models']['test']['limit']['context'],65536)
        self.assertTrue(config['agents']['cluster-lead']['disabled'])
        self.assertFalse(config['agents']['build']['disabled']);self.assertFalse(config['agents']['plan']['disabled'])
        for name in ('build','plan','title','summary','compaction'):self.assertEqual(config['agents'][name]['model'],'modelhub/test')
        self.assertEqual(config['policies'][-1],{'action':'provider.use','resource':'modelhub','effect':'allow'})

    def test_jsonc_parser_preserves_urls_and_strings(self):
        path=self.path/'test.jsonc';path.write_text('{// note\n"url":"http://localhost/", "text":"x, }", "agents":{"team":{"mode":"primary"},},}',encoding='utf-8')
        data=terminal.config_json(path)
        self.assertEqual(data['url'],'http://localhost/');self.assertEqual(data['text'],'x, }')

    def test_original_config_is_backed_up_without_changes(self):
        home=self.path/'home';source=home/'.config/opencode';source.mkdir(parents=True)
        original=b'{"agents":{"old":{"mode":"primary"}}}'
        (source/'opencode.json').write_bytes(original)
        with patch.object(core,'HOME',home),patch.object(core,'STATE',self.path/'state'):
            directory=terminal.prepare_simple_directory()
        self.assertTrue((directory/'opencode').is_dir())
        self.assertEqual((source/'opencode.json').read_bytes(),original)
        self.assertEqual((self.path/'state/opencode-original-backup/opencode.json').read_bytes(),original)

    def test_installed_opencode_v2_accepts_config_and_lists_only_build_plan(self):
        exe=core.HOME/'AppData/Roaming/npm/node_modules/@opencode/cli/bin/opencode.exe'
        if not exe.is_file():self.skipTest('Installed OpenCode V2 is not available.')
        env=dict(os.environ)
        for name,folder in [('XDG_DATA_HOME','data'),('XDG_CONFIG_HOME','config'),('XDG_CACHE_HOME','cache'),('XDG_STATE_HOME','state')]:
            target=self.path/folder;target.mkdir();env[name]=str(target)
        env['OPENCODE_CONFIG_CONTENT']=json.dumps(terminal.config('test','http://127.0.0.1:19999',self.p,self.path))
        password=secrets.token_urlsafe(24);env['OPENCODE_SERVER_PASSWORD']=password
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=(self.path/'server.log').open('w')
        process=subprocess.Popen([str(exe),'serve','--hostname','127.0.0.1','--port',str(port)],env=env,cwd=self.path,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(50):
                if process.poll() is not None:self.fail('OpenCode exited before the config check.')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.1)
            http=build_opener(ProxyHandler({}));auth='Basic '+base64.b64encode(('opencode:'+password).encode()).decode()
            query=urlencode({'location[directory]':str(self.path)})
            def read(path):
                for attempt in range(40):
                    try:
                        with http.open(Request(f'http://127.0.0.1:{port}'+path+'?'+query,headers={'Authorization':auth}),timeout=10) as response:return json.load(response)['data']
                    except HTTPError as exc:
                        if exc.code!=503 or attempt==39:raise
                        time.sleep(.2)
            agents=read('/api/agent');visible={a['id'] for a in agents if not a.get('hidden')}
            self.assertEqual(visible,{'build','plan'})
            model=read('/api/model/default');self.assertEqual(model['providerID'],'modelhub')
            self.assertEqual(model['limit']['context'],65536);self.assertEqual(model['settings']['baseURL'],'http://127.0.0.1:19999/v1')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
            log.close()

if __name__=='__main__':unittest.main(verbosity=2)
