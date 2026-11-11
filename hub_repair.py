"""Per-model GPU trials in isolated, owned servers. No global driver/BIOS edits."""
from datetime import datetime,timezone
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid
import winreg
import hub_core as core

class Cancelled(Exception):pass
class TrialFailure(Exception):
    def __init__(self,message,evidence=None):super().__init__(message);self.evidence=evidence or {}

def fingerprint_details(row,profile):
    drivers=[]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}') as parent:
            for i in range(winreg.QueryInfoKey(parent)[0]):
                name=winreg.EnumKey(parent,i)
                if not name.isdigit():continue
                try:
                    with winreg.OpenKey(parent,name) as key:drivers.append(str(winreg.QueryValueEx(key,'DriverVersion')[0]))
                except OSError:pass
    except OSError:pass
    files=[]
    for p in row.get('files',[]):
        try:
            s=Path(p).stat();files.append((str(p),s.st_size,s.st_mtime_ns))
        except OSError:files.append((str(p),'missing'))
    if row.get('manifest'):
        try:files.append(('manifest',hashlib.sha256(Path(row['manifest']).read_bytes()).hexdigest()))
        except OSError:files.append(('manifest','missing'))
    if row['runtime']=='Strata':
        source=core.read_json(row['source']);runtime=Path(source.get('cwd',str(core.STRATA/'terminal/current')))/'serve/server.py'
        files.append(('source_config',hashlib.sha256(Path(row['source']).read_bytes()).hexdigest()))
    elif row['runtime']=='Ollama':runtime=Path(shutil.which('ollama.exe') or core.HOME/'AppData/Local/Programs/Ollama/ollama.exe')
    else:runtime=Path(profile.get('llama_exe',''))
    try:
        s=runtime.stat();files.append(('runtime',str(runtime),s.st_size,s.st_mtime_ns))
    except OSError:files.append(('runtime','missing'))
    library_root=runtime.parent/'lib/ollama' if row['runtime']=='Ollama' else runtime.parent
    for path in sorted(library_root.glob('**/*.dll')):
        try:
            s=path.stat();files.append(('library',str(path),s.st_size,s.st_mtime_ns))
        except OSError:pass
    if row['runtime']=='Strata':
        engine_roots=[core.STRATA/'components/engine']
        executable=core.read_json(row['source']).get('exe','')
        if executable and not executable.startswith('component:'):engine_roots.append(Path(executable).parent)
        for root in engine_roots:
            for path in sorted(root.glob('**/*')):
                if path.suffix.lower() not in ('.exe','.dll'):continue
                try:
                    s=path.stat();files.append(('engine',str(path),s.st_size,s.st_mtime_ns))
                except OSError:pass
    environment={key:os.environ.get(key) for key in ('HIP_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','GGML_VK_VISIBLE_DEVICES','HSA_OVERRIDE_GFX_VERSION','OLLAMA_GPU_OVERHEAD')}
    validated=core.validate_profile(profile)
    memory_settings={key:validated[key] for key in ('context','parallel','kv','vision','gpu_layers','threads','llama_exe')}
    payload={'files':files,'gpu':core.gpu_hardware(),'drivers':drivers,'memory_settings':memory_settings,'gpu_environment':environment}
    return payload

def fingerprint(row,profile):
    return hashlib.sha256(json.dumps(fingerprint_details(row,profile),sort_keys=True,default=str).encode()).hexdigest()

def recipe_changes(row,profile,recipe):
    changes=[]
    previous=recipe.get('choice',{}).get('profile',{})
    current=core.validate_profile(profile)
    for key in ('context','parallel','kv','vision','gpu_layers','threads','llama_exe'):
        if previous.get(key)!=current.get(key):changes.append(f'{key}: {previous.get(key)} → {current.get(key)}')
    saved=recipe.get('fingerprint_details')
    if saved:
        now=fingerprint_details(row,profile)
        for key,label in [('files','Model or runner files'),('gpu','Graphics configuration'),('drivers','Graphics driver'),('gpu_environment','GPU environment')]:
            if json.dumps(saved.get(key),sort_keys=True)!=json.dumps(now.get(key),sort_keys=True):changes.append(label+' changed or could not be read the same way.')
    if recipe.get('status')!='passed':changes.append(recipe.get('last_failure','The previous model session needs a new check.'))
    return changes or ['The saved identity check differs. This older record cannot identify which item changed.']

def recipe_state(row,profile,recipe):
    if not recipe:return 'Not tested'
    if recipe.get('status')!='passed':return 'Needs a new test'
    if recipe.get('fingerprint')!=fingerprint(row,profile):return 'Changed — test again'
    return 'Test passed'

def report_summary():
    last=core.read_json(core.STATE/'last_gpu_test.json');result=last.get('result') or {}
    return {'model':last.get('model'),'status':result.get('status','not completed'),
        'tested_at_utc':result.get('tested_at_utc'),'backend':result.get('choice',{}).get('backend'),
        'context':result.get('choice',{}).get('profile',{}).get('context'),'evidence':result.get('evidence',{}),
        'attempts':[{k:a.get(k) for k in ('backend','context','evidence')} for a in result.get('attempts',[])],
        'optimized':result.get('optimized',False),'benchmark':result.get('benchmark',{}),'context_probe':result.get('context_probe',{}),
        'performance_goal':result.get('performance_goal',{}),
        'comparison':[{k:a.get(k) for k in ('backend','context','benchmark','context_probe','evidence')} for a in result.get('comparison',[])]}

def save_recipe(row,result,previous_profile=None):
    if result.get('status')!='passed' or not result.get('gpu_confirmed') or not result.get('answers_passed'):
        raise ValueError('A failed or incomplete test cannot become the saved setup.')
    path=core.STATE/'verified_recipes.json';data=core.read_json(path)
    previous=data.get(row['id']);history=core.read_json(core.STATE/'recipe_history.json')
    history.setdefault(row['id'],[]).append({'recipe':previous,'profile':previous_profile})
    core.save_json(core.STATE/'recipe_history.json',history)
    data[row['id']]=result;core.save_json(path,data)

def undo_recipe(model_id):
    history=core.read_json(core.STATE/'recipe_history.json');items=history.get(model_id,[])
    if not items:raise ValueError('There is no saved setup to undo for this model.')
    entry=items.pop();previous=entry['recipe'];data=core.read_json(core.STATE/'verified_recipes.json')
    if previous is None:data.pop(model_id,None)
    else:data[model_id]=previous
    core.save_json(core.STATE/'verified_recipes.json',data);core.save_json(core.STATE/'recipe_history.json',history)
    return entry['profile']

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def port_owned_by(port,pid):
    dll=C.WinDLL('iphlpapi',use_last_error=True)
    dll.GetExtendedTcpTable.argtypes=[C.c_void_p,C.POINTER(W.DWORD),W.BOOL,W.ULONG,C.c_int,W.ULONG]
    dll.GetExtendedTcpTable.restype=W.DWORD
    size=W.DWORD();code=dll.GetExtendedTcpTable(None,C.byref(size),False,2,5,0)
    if code==5:raise PermissionError('Windows blocked the server-owner check. Run the desktop app in your normal Windows session; this restricted process cannot safely run GPU trials.')
    if code not in (0,122) or not 4<=size.value<=16*1024*1024:return False
    buf=C.create_string_buffer(size.value)
    code=dll.GetExtendedTcpTable(buf,C.byref(size),False,2,5,0)
    if code==5:raise PermissionError('Windows blocked the server-owner check. No model trial can be confirmed in this session.')
    if code:return False
    count=struct.unpack_from('<I',buf.raw)[0]
    for i in range(min(count,(len(buf)-4)//24)):
        state,addr,local_port,remote_addr,remote_port,owner=struct.unpack_from('<6I',buf.raw,4+i*24)
        if state==2 and socket.ntohs(local_port&0xffff)==port and owner==pid:return True
    return False

def set_arg(args,key,value):
    args=list(args)
    if key in args:
        i=args.index(key)
        if i+1>=len(args):raise ValueError('Broken runtime setting: '+key)
        args[i+1]=str(value)
    else:args.extend([key,str(value)])
    return args

def candidates(row,profile,smaller=True):
    contexts=[profile['context']]
    if smaller and profile['context']>8192:contexts.append(8192)
    backends=['auto','vulkan','rocm_v7_1'] if row['runtime']=='Ollama' else ['existing']
    choices=[{'backend':b,'profile':dict(profile,context=c,output=min(profile['output'],c//4),parallel=1)} for c in contexts for b in backends]
    if row['runtime']=='GGUF file':
        for choice in choices:choice['profile']['gpu_layers']=999
    return choices

class Session:
    def __init__(self,row,choice):
        self.row=dict(row);self.choice=choice;self.port=free_port();self.base='http://127.0.0.1:'+str(self.port)
        self.model=row['name'];self.process=None;self.log=None;self.stream=None
        self.stop_lock=threading.Lock()

    def start(self):
        root=core.STATE/'gpu_trials'/uuid.uuid4().hex;root.mkdir(parents=True)
        self.log=root/'runtime.log';env=dict(os.environ);p=self.choice['profile'];runtime=self.row['runtime']
        if runtime=='Ollama':
            exe=shutil.which('ollama.exe') or str(core.HOME/'AppData/Local/Programs/Ollama/ollama.exe')
            env.update(OLLAMA_HOST=self.base,OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_NO_CLOUD='1')
            env.pop('OLLAMA_LLM_LIBRARY',None)
            if self.choice['backend']!='auto':env['OLLAMA_LLM_LIBRARY']=self.choice['backend']
            env['OLLAMA_VULKAN']='1';command=[exe,'serve'];cwd=str(core.HERE)
        elif runtime=='Strata':
            conf=core.read_json(self.row['source']);args=conf.get('args')
            if not isinstance(args,list):raise ValueError('The Strata profile is not complete.')
            if '--max-context' not in args:raise ValueError('This Strata profile needs a supported context setting before it can be tested.')
            conf['args']=set_arg(args,'--max-context',p['context']);conf['port']=self.port
            conf['host']='127.0.0.1';conf['log']=str(root/'engine.log');conf['open_browser']=False
            conf['args_written']=conf['args'];conf.pop('api_key',None)
            config=root/'model.json';core.save_json(config,conf)
            cwd=conf.get('cwd',str(core.STRATA/'terminal/current'))
            script=Path(cwd)/'serve/server.py'
            python=core.STRATA/'terminal/current/python/python.exe'
            if not script.is_file() or not python.is_file():raise ValueError('The installed Strata runner could not be found.')
            command=[str(python),str(script),'--engine','strata','--config',str(config),'--port',str(self.port),'--parallel','1']
        elif runtime=='GGUF file':
            exe=Path(p['llama_exe'])
            if not exe.is_file():raise ValueError('Choose llama-server.exe under advanced settings first.')
            command=[str(exe),'-m',self.row['files'][0],'-c',str(p['context']),'-np','1','-ngl','999',
                '-t',str(p['threads']),'--host','127.0.0.1','--port',str(self.port),'--alias',self.model]
            cwd=str(exe.parent)
        else:raise ValueError('This runtime is not supported by the guided test.')
        self.stream=self.log.open('w',encoding='utf-8')
        try:self.process=subprocess.Popen(command,cwd=cwd,env=env,stdout=self.stream,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:self.stream.close();raise

    def log_text(self):
        texts=[]
        if self.log:
            for path in (self.log,self.log.parent/'engine.log'):
                try:
                    with path.open('rb') as f:f.seek(max(0,path.stat().st_size-262144));texts.append(f.read().decode('utf-8','replace'))
                except OSError:pass
        return '\n'.join(texts)

    def ready(self,cancel,timeout=1200):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if cancel.is_set():raise Cancelled('Test stopped. No setup was saved.')
            if self.process.poll() is not None:raise RuntimeError('The model runner stopped before it was ready. See the test log.')
            try:
                path='/api/version' if self.row['runtime']=='Ollama' else '/health'
                status=core.api(self.base,path,timeout=2)
                if (self.row['runtime']=='Ollama' or status.get('status') in ('ok','ready')) and port_owned_by(self.port,self.process.pid):return
            except PermissionError:raise
            except (OSError,ValueError):pass
            cancel.wait(.5)
        raise TimeoutError('The model did not become ready in 20 minutes.')

    def stop(self):
        # Only the private process created by this session, never a user's existing server.
        with self.stop_lock:
            if self.process and self.process.poll() is None:
                subprocess.run(['taskkill.exe','/PID',str(self.process.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
                try:self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:pass
            if self.stream:self.stream.close()

def used_backend(log):
    matches=re.findall(r'using device\s+(ROCm|Vulkan|CUDA)\d',log,re.I)
    if not matches:matches=re.findall(r'(ROCm|Vulkan|CUDA)\d\s+model buffer',log,re.I)
    if not matches:return None
    return {'rocm':'rocm_v7_1','vulkan':'vulkan','cuda':'cuda'}[matches[-1].lower()]

def assess(session,responses):
    p=session.choice['profile'];runtime=session.row['runtime'];log=session.log_text()
    if len(responses)!=2 or not all(r.get('finished') and r.get('tokens',0)>0 and r.get('text','').strip() for r in responses):
        raise ValueError('The model did not finish both answer tests.')
    if responses[0]['text'].strip().strip('` .!').upper()!='READY' or responses[1]['text'].strip().strip('` .!')!='4':
        raise ValueError('The model answered, but did not pass the two simple answer checks.')
    facts={}
    if runtime=='Ollama':
        model=next((r for r in core.api(session.base,'/api/ps').get('models',[]) if r['name']==session.model),None)
        if not model or not model.get('size') or not model.get('size_vram'):raise ValueError('GPU model memory could not be confirmed. This is not a passing GPU test.')
        ratio=model['size_vram']/model['size']
        if ratio<.95:raise ValueError('Less than 95% of model memory was reported on the GPU. This needs review rather than a passing label.')
        if model.get('context_length')!=p['context']:raise ValueError('The requested conversation size could not be confirmed.')
        actual=used_backend(log)
        facts={'graphics_bytes':model['size_vram'],'model_memory_bytes':model['size'],'context':model['context_length'],'backend_used':actual}
        expected=session.choice['backend']
        if expected=='vulkan' and actual!='vulkan':raise ValueError('The runner did not confirm that the model used the requested Vulkan device.')
        if expected.startswith('rocm') and actual!='rocm_v7_1':raise ValueError('The runner did not confirm that the model used the requested ROCm device.')
    elif runtime=='Strata':
        if not re.search(r'\d+ MiB of VRAM free with everything loaded',log):raise ValueError('This Strata runner did not provide fresh GPU-load proof. It cannot be marked as tested.')
        health=core.api(session.base,'/health')
        if health.get('max_context')!=p['context']:raise ValueError('The Strata server did not confirm the requested conversation size.')
        facts={'fresh_gpu_load_log':True,'context_confirmed':p['context']}
    else:
        layers=re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU',log)
        if not layers or int(layers[-1][0])!=int(layers[-1][1]) or int(layers[-1][0])==0:raise ValueError('The log did not confirm that all model layers were put on the GPU.')
        contexts=re.findall(r'n_ctx_per_seq\s*=\s*(\d+)',log)
        if not contexts or int(contexts[-1])!=p['context']:raise ValueError('The llama-server log did not confirm the requested conversation size.')
        facts={'offloaded_layers':int(layers[-1][0]),'context_confirmed':p['context']}
    return facts

def answer(session,prompt):
    p=session.choice['profile'];started=time.monotonic()
    if session.row['runtime']=='Ollama':
        result=core.api(session.base,'/api/generate',{'model':session.model,'prompt':prompt,'stream':False,'keep_alive':'10m',
            'options':{'num_ctx':p['context'],'num_predict':256,'temperature':0}},timeout=600)
        elapsed=time.monotonic()-started;tokens=result.get('eval_count',0);duration=result.get('eval_duration',0)
        return {'text':result.get('response',''),'tokens':tokens,'prompt_tokens':result.get('prompt_eval_count'),'finished':result.get('done') is True and result.get('done_reason')!='length',
            'seconds':elapsed,'job_tps':tokens/elapsed if tokens and elapsed else None,'generation_tps':tokens*1e9/duration if tokens and duration else None}
    result=core.api(session.base,'/v1/chat/completions',{'model':session.model,'messages':[{'role':'user','content':prompt}],
        'max_tokens':256,'temperature':0,'stream':False},timeout=600)
    elapsed=time.monotonic()-started;tokens=result.get('usage',{}).get('completion_tokens',0)
    return {'text':result['choices'][0]['message'].get('content',''),'tokens':tokens,'prompt_tokens':result.get('usage',{}).get('prompt_tokens'),'finished':result['choices'][0].get('finish_reason')=='stop',
        'seconds':elapsed,'job_tps':tokens/elapsed if tokens and elapsed else None,'generation_tps':result.get('timings',{}).get('predicted_per_second')}

def run_choice(row,choice,cancel,progress,keep=False,benchmark=False,context_probe=False):
    session=Session(row,choice);watch_done=threading.Event();retaining=False
    try:
        progress('Starting '+choice['backend']+' at '+str(choice['profile']['context'])+' remembered tokens…')
        session.start()
        def watch():
            while not watch_done.wait(.25):
                if cancel.is_set():session.stop();return
        threading.Thread(target=watch,daemon=True).start()
        session.ready(cancel)
        if row['runtime']=='Ollama':
            tags=core.api(session.base,'/api/tags').get('models',[])
            if not any(m['name']==row['name'] for m in tags):raise ValueError('The model is not installed in this Ollama store. No download was requested.')
            import hub_chat
            hub_chat.verify_local_ollama(session.base,row['name'])
            profile=choice['profile']
            alias='modelhub-tested-'+hashlib.sha256(json.dumps([row['id'],profile],sort_keys=True).encode()).hexdigest()[:12]+':latest'
            core.api(session.base,'/api/create',{'from':row['name'],'model':alias,'parameters':{
                'num_ctx':profile['context'],'num_gpu':-1,'num_predict':profile['output'],'temperature':profile['temperature'],'top_p':profile['top_p']},'stream':False},timeout=60)
            session.model=alias
        responses=[]
        for prompt in ('Reply with exactly the word READY.','What is 2 + 2? Reply with just the number.'):
            if cancel.is_set():raise Cancelled('Test stopped.')
            progress('Checking that the model can finish a short answer…');responses.append(answer(session,prompt))
        if cancel.is_set():raise Cancelled('Test stopped.')
        facts=assess(session,responses)
        result={'status':'passed','gpu_confirmed':True,'answers_passed':True,'choice':choice,'fingerprint':fingerprint(row,choice['profile']),
            'tested_at_utc':datetime.now(timezone.utc).isoformat(),'evidence':facts,'runtime':row['runtime'],'model':row['name']}
        result['fingerprint_details']=fingerprint_details(row,choice['profile'])
        if context_probe:
            context=choice['profile']['context']
            progress(f'Checking a large input for the {context:,}-token context. This may take several minutes…')
            probe=answer(session,'This is a memory-size test. Ignore the padding. Follow only the final instruction.\nPadding:\n'+
                ' tree'*int(context*.65)+'\nEnd of padding. Reply with exactly READY.')
            if cancel.is_set():raise Cancelled('Test stopped.')
            if not probe.get('finished') or probe['text'].strip().strip('` .!').upper()!='READY':raise ValueError('The model did not finish the long-input check.')
            if not probe.get('prompt_tokens') or probe['prompt_tokens']<context*.5:raise ValueError('The runner did not confirm enough input tokens for the long-context check. A large context cannot be marked as tested.')
            result['context_probe']={'input_tokens':probe['prompt_tokens'],'seconds':probe['seconds'],'context_limit':context,'scope':'large input sample, not a full-window or coding-quality guarantee'}
        if benchmark:
            progress('Measuring a short coding answer. This is a starting-point test, not every possible job…')
            sample=answer(session,'Write a plain Python function named dedupe that removes duplicate strings from a list while keeping their order. Include a docstring and three small assert examples. Do not use markdown or explain outside the code.')
            if cancel.is_set():raise Cancelled('Test stopped.')
            result['benchmark']={key:sample.get(key) for key in ('tokens','seconds','job_tps','generation_tps','finished')}
            result['benchmark']['comparable']=bool(sample.get('finished') and sample.get('tokens',0)>=16 and sample.get('job_tps'))
            result['evidence']=assess(session,responses)  # Confirm GPU/context again after the longer answer.
        if cancel.is_set():raise Cancelled('Test stopped. No setup was saved.')
        if keep:retaining=True;return result,session
        return result,None
    except Exception as exc:
        if cancel.is_set() or isinstance(exc,Cancelled):raise Cancelled('Test stopped. No setup was saved.')
        import hub_gpu
        raise TrialFailure(str(exc),hub_gpu.parse_runtime_log(session.log_text(),row['runtime'])) from exc
    finally:
        watch_done.set()
        if not retaining:session.stop()

def run_trials(row,profile,cancel,progress,smaller=True):
    attempts=[]
    for choice in candidates(row,profile,smaller):
        if cancel.is_set():raise Cancelled('Test stopped. No setup was saved.')
        memory=core.system_memory()
        if memory['available']<profile['reserve_gib']*core.GiB or memory['commit_free']<profile['reserve_gib']*core.GiB:
            raise ValueError('Windows memory is below your safety reserve. Close unused models/apps and test again. Nothing was saved.')
        try:
            result,_=run_choice(row,choice,cancel,progress)
            result['attempts']=attempts;return result
        except Exception as exc:
            if cancel.is_set() or isinstance(exc,Cancelled):raise Cancelled('Test stopped. No setup was saved.')
            attempts.append({'backend':choice['backend'],'context':choice['profile']['context'],'error':str(exc)[:800],'evidence':getattr(exc,'evidence',{})})
            progress('That setup did not pass. Trying the next supported setup…')
            cancel.wait(1)
    return {'status':'failed','attempts':attempts,'model':row['name'],'gpu_confirmed':False,'answers_passed':False}

def optimize(row,profile,cancel,progress,goal_context=65536,minimum_tps=25,target_tps=50,minimum_context=None):
    """Find the fastest measured supported setup within the requested context range."""
    maximum=next((int(v) for k,v in row.get('meta',{}).items() if k.endswith('.context_length')),goal_context)
    target=min(goal_context,maximum)
    if minimum_context is None:minimum_context=min(goal_context,32768) if goal_context>=32768 else min(goal_context,8192)
    if target<2048:raise ValueError('This model context is too small for the coding starting-point test.')
    baseline=core.validate_profile(dict(profile,context=target,output=min(4096,target//4),parallel=1,temperature=.2,top_p=.9))
    if minimum_tps<1 or target_tps<minimum_tps:raise ValueError('Use a positive speed minimum and a target at least as high.')
    attempts=[];passing=[];seen=set();tier=None
    context_sizes=list(dict.fromkeys([target]+[c for c in (65536,32768,16384,8192) if c<target]))
    choices=[choice for context in context_sizes for choice in candidates(row,dict(baseline,context=context,output=min(baseline['output'],context//4)),smaller=False)]
    for choice in choices:
        context=choice['profile']['context']
        if tier is not None and context!=tier and context<minimum_context:
            # Finish every eligible tier, even when 25 or 50 tok/s is already met.
            # Smaller contexts are fallback diagnostics only if none qualifies.
            if any(r['choice']['profile']['context']>=minimum_context and r.get('benchmark',{}).get('comparable') and (r['benchmark'].get('generation_tps') or 0)>=minimum_tps for r in passing):break
        tier=context
        if (choice['backend'],context) in seen:continue
        if cancel.is_set():raise Cancelled('Optimization stopped. Nothing saved.')
        memory=core.system_memory()
        if min(memory['available'],memory['commit_free'])<baseline['reserve_gib']*core.GiB:raise ValueError('Windows memory is below the safety reserve. Stop unused models/apps and optimize again.')
        try:
            result,_=run_choice(row,choice,cancel,progress,benchmark=True,context_probe=context>=32768)
            passing.append(result)
            actual=result['evidence'].get('backend_used')
            if actual:seen.add((actual,context))
            progress('This setup passed. Comparing the next supported GPU route…')
        except Exception as exc:
            if cancel.is_set() or isinstance(exc,Cancelled):raise Cancelled('Optimization stopped. Nothing saved.')
            attempts.append({'backend':choice['backend'],'context':context,'error':str(exc)[:800],'evidence':getattr(exc,'evidence',{})})
        cancel.wait(1)
    if not passing:return {'status':'failed','optimized':True,'attempts':attempts,'model':row['name'],'gpu_confirmed':False,'answers_passed':False}
    comparable=[r for r in passing if r.get('benchmark',{}).get('comparable')]
    measured=[r for r in comparable if r['benchmark'].get('generation_tps')]
    eligible=[r for r in measured if r['benchmark']['generation_tps']>=minimum_tps and r['choice']['profile']['context']>=minimum_context]
    if eligible:
        winner=max(eligible,key=lambda r:(r['benchmark']['generation_tps'],r['choice']['profile']['context']))
    elif measured:winner=max(measured,key=lambda r:r['benchmark']['generation_tps'])
    elif comparable:winner=max(comparable,key=lambda r:r['benchmark']['job_tps'])
    else:winner=passing[0]
    winner['optimized']=True;winner['attempts']=attempts
    winner['comparison']=[{'backend':r['choice']['backend'],'context':r['choice']['profile']['context'],'benchmark':r.get('benchmark',{}),'context_probe':r.get('context_probe',{}),'evidence':r['evidence']} for r in passing]
    speed=winner.get('benchmark',{}).get('generation_tps') if winner.get('benchmark',{}).get('comparable') else None
    winner['performance_goal']={'minimum_tps':minimum_tps,'target_tps':target_tps,'measured_writing_tps':speed,
        'minimum_met':bool(speed is not None and speed>=minimum_tps),'target_met':bool(speed is not None and speed>=target_tps),
        'minimum_context':minimum_context,'context_goal_met':winner['choice']['profile']['context']>=minimum_context}
    winner['recommendation']=('Fastest measured supported setup within your context range. The aspiration is not a speed cap; actual coding speed varies.' if eligible else
        'GPU checks passed, but no measured setup met the writing-speed minimum. Nothing should be saved automatically.' if comparable else
        'A GPU-confirmed starting point. Comparable speed readings were not available; the speed goal is unverified.')
    return winner
