"""Local model inventory, launch profiles, telemetry and a loopback OpenCode gateway."""
import ctypes as C
from ctypes import wintypes as W
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlparse
import winreg
import legacy_monitor as native

HOME = Path(os.environ['USERPROFILE'])
# LOCALAPPDATA can be redirected inside the Codex execution sandbox.
STRATA = HOME / 'AppData' / 'Local' / 'Strata'
HERE = Path(__file__).resolve().parent
STATE = HERE / 'data'
GiB = 1024**3

def read_json(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError): return {} if default is None else default

def save_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8'); os.replace(temp, path)

def local_url(value):
    value = value.strip().rstrip('/')
    if '://' not in value: value = 'http://' + value
    u = urlparse(value)
    if u.scheme != 'http' or u.hostname not in ('localhost', '127.0.0.1', '::1') or u.username or u.password or u.query or u.fragment or u.path:
        raise ValueError('Use a local HTTP server address without a path.')
    _ = u.port
    return value

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise ValueError('Local server redirects are disabled')

HTTP = build_opener(ProxyHandler({}), NoRedirect())

def api(base, path, body=None, timeout=4):
    base = local_url(base)
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json'})
    with HTTP.open(req, timeout=timeout) as response: return json.load(response)

class Memory(C.Structure):
    _fields_ = [('length', W.DWORD), ('load', W.DWORD)] + [(k, C.c_ulonglong) for k in
        ('total', 'available', 'commit_limit', 'commit_free', 'virtual_total', 'virtual_free', 'extended')]

class PerformanceInfo(C.Structure):
    _fields_=[('cb',W.DWORD)]+[(k,C.c_size_t) for k in ('CommitTotal','CommitLimit','CommitPeak','PhysicalTotal',
        'PhysicalAvailable','SystemCache','KernelTotal','KernelPaged','KernelNonpaged','PageSize')]+[(k,W.DWORD) for k in ('HandleCount','ProcessCount','ThreadCount')]

def system_memory():
    m = Memory(); m.length = C.sizeof(m)
    if not native.K.GlobalMemoryStatusEx(C.byref(m)): raise C.WinError()
    result={k: getattr(m, k) for k in ('total', 'available', 'commit_limit', 'commit_free', 'load')}
    info=PerformanceInfo();info.cb=C.sizeof(info)
    native.P.GetPerformanceInfo.argtypes=[C.POINTER(PerformanceInfo),W.DWORD]
    if native.P.GetPerformanceInfo(C.byref(info),info.cb):
        result['commit_limit']=info.CommitLimit*info.PageSize
        result['commit_free']=max(0,info.CommitLimit-info.CommitTotal)*info.PageSize
    return result

def gpu_hardware():
    results = []
    key = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as parent:
            for i in range(winreg.QueryInfoKey(parent)[0]):
                name = winreg.EnumKey(parent, i)
                if not name.isdigit(): continue
                try:
                    with winreg.OpenKey(parent, name) as child:
                        desc = winreg.QueryValueEx(child, 'DriverDesc')[0]
                        try: capacity = winreg.QueryValueEx(child, 'HardwareInformation.qwMemorySize')[0]
                        except OSError: capacity = None
                        results.append({'name':desc, 'capacity':capacity})
                except OSError: pass
    except OSError: pass
    return results

def process_inventory():
    snap = native.K.CreateToolhelp32Snapshot(2, 0)
    if snap == C.c_void_p(-1).value: raise C.WinError()
    records = []
    try:
        e = native.Entry(); e.size = C.sizeof(e)
        ok = native.K.Process32FirstW(snap, C.byref(e))
        while ok:
            records.append({'pid':int(e.pid), 'parent':int(e.parent), 'name':e.exe})
            ok = native.K.Process32NextW(snap, C.byref(e))
    finally: native.K.CloseHandle(snap)
    candidates = []
    for row in records:
        name = row['name'].lower()
        if not any(s in name for s in ('ollama','llama','strata','opencode','python','node','llmster','lm studio','lm-studio')): continue
        h = native.K.OpenProcess(0x1000 | 0x10, False, row['pid']) or native.K.OpenProcess(0x1000, False, row['pid'])
        row.update(path='', command='', birth=None, ram=None, private=None)
        if h:
            try:
                buf = C.create_unicode_buffer(32768); length = W.DWORD(len(buf))
                if native.K.QueryFullProcessImageNameW(h, 0, buf, C.byref(length)): row['path'] = buf.value
                row['command'] = native.command_line(h); row['birth'] = native.birth(h)
                mem = native.Mem(); mem.cb = C.sizeof(mem)
                if native.P.GetProcessMemoryInfo(h, C.byref(mem), mem.cb): row.update(ram=mem.working, private=mem.private)
            finally: native.K.CloseHandle(h)
        combined = (name + ' ' + row['path'] + ' ' + row['command']).lower()
        if 'opencode' in combined: row['category'] = 'OpenCode'
        elif 'strata' in combined: row['category'] = 'Strata'
        elif 'ollama' in combined: row['category'] = 'Ollama'
        elif 'llama' in name or 'llmster' in combined or 'lm studio' in combined or 'lm-studio' in combined: row['category'] = 'Model engine'
        else: continue
        candidates.append(row)
    # Include subprocesses used by OpenCode (tools, language servers, workers).
    owned = {r['pid'] for r in candidates if r['category'] == 'OpenCode'}
    changed = True
    while changed:
        changed = False
        for row in records:
            if row['parent'] in owned and row['pid'] not in owned:
                owned.add(row['pid']); changed = True
    existing = {r['pid'] for r in candidates}
    for row in records:
        if row['pid'] not in owned or row['pid'] in existing: continue
        row.update(category='OpenCode child', ram=None, private=None, birth=None, path='', command='')
        h = native.K.OpenProcess(0x1000 | 0x10, False, row['pid'])
        if h:
            try:
                row['birth'] = native.birth(h)
                mem = native.Mem(); mem.cb = C.sizeof(mem)
                if native.P.GetProcessMemoryInfo(h, C.byref(mem), mem.cb): row.update(ram=mem.working, private=mem.private)
            finally: native.K.CloseHandle(h)
        candidates.append(row)
    return candidates

def stop_process(row):
    if row.get('birth') is None: raise RuntimeError('Process identity unavailable; cannot safely stop it.')
    h = native.K.OpenProcess(0x1000 | 1, False, row['pid'])
    if not h: raise C.WinError()
    try:
        if native.birth(h) != row['birth']: raise RuntimeError('Process changed. Refresh before stopping.')
        if not native.K.TerminateProcess(h, 0): raise C.WinError()
    finally: native.K.CloseHandle(h)

def gpu_usage():
    """System-wide Windows performance counters, never process-only DXGI usage."""
    command = r"$ErrorActionPreference='Stop'; (Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage').CounterSamples | Select-Object InstanceName,Path,CookedValue | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
            capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode: return {'available':False, 'reason':'Windows GPU counters are not accessible.'}
        samples = json.loads(result.stdout); samples = samples if isinstance(samples,list) else [samples]
        adapters = {}
        for sample in samples:
            item = adapters.setdefault(sample['InstanceName'], {'dedicated':0,'shared':0})
            item['dedicated' if 'dedicated' in sample['Path'].lower() else 'shared'] = max(0, sample['CookedValue'])
        return {'available':bool(adapters), 'adapters':adapters, 'time':time.time()}
    except Exception: return {'available':False, 'reason':'Windows GPU counters unavailable; GPU protection cannot be verified.'}

def read_gguf(path):
    """Read metadata and tensor dimensions only, without loading weights."""
    formats = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
    with open(path, 'rb') as f:
        def unpack(fmt):
            n = struct.calcsize('<'+fmt); data=f.read(n)
            if len(data)!=n: raise ValueError('Truncated GGUF header')
            return struct.unpack('<'+fmt,data)[0]
        def string(keep=True):
            n=unpack('Q')
            if n>64*1024*1024: raise ValueError('Invalid GGUF string')
            if keep: return f.read(n).decode('utf-8','replace')
            f.seek(n,1)
        def value(kind, keep=False):
            if kind==8: return string(keep)
            if kind==9:
                subtype=unpack('I'); n=unpack('Q')
                if n>100_000_000: raise ValueError('Invalid GGUF array')
                if subtype in formats: f.seek(struct.calcsize('<'+formats[subtype])*n,1)
                else:
                    for _ in range(n): value(subtype)
                return None
            if kind not in formats: raise ValueError('Unknown GGUF type')
            return unpack(formats[kind])
        if f.read(4)!=b'GGUF': raise ValueError('Not a GGUF model')
        version=unpack('I')
        if version not in (2,3): raise ValueError('Unsupported GGUF version')
        nt=unpack('Q'); nk=unpack('Q')
        if nt>2_000_000 or nk>100_000: raise ValueError('Invalid GGUF counts')
        meta={}
        for _ in range(nk):
            key=string(); kind=unpack('I')
            keep=key.startswith('general.') or key.endswith(('.context_length','.block_count','.embedding_length','.head_count_kv','.key_length','.value_length','.head_count'))
            item=value(kind,keep)
            if keep and item is not None: meta[key]=item
        count=0
        for _ in range(nt):
            string(False); nd=unpack('I')
            if nd>8: raise ValueError('Invalid tensor dimensions')
            size=1
            for _ in range(nd): size*=unpack('Q')
            count+=size; unpack('I'); unpack('Q')
        meta['tensor_parameters']=count
        return meta

def parameter_number(label):
    match=re.search(r'(\d+(?:\.\d+)?)\s*([bBmM])(?:\b|_)',str(label or ''))
    return float(match[1])*(1e9 if match[2].lower()=='b' else 1e6) if match else None

def model_files(path):
    path=Path(path)
    if not path.exists(): return []
    match=re.match(r'(.*)-\d{5}-of-(\d{5})\.gguf$',path.name,re.I)
    if match: return sorted(path.parent.glob(match[1]+'-?????-of-'+match[2]+'.gguf'))
    return [path]

def strata_inventory():
    rows=[]; seen=set()
    # Install profiles persist when engines are stopped. Launch configs preserve custom profiles too.
    paths=sorted((STRATA/'install').glob('strata-*.json')) + sorted((STRATA/'launch').glob('*.json'))
    for path in paths:
        conf=read_json(path); name=conf.get('model_name'); args=conf.get('args',[])
        if not name or not isinstance(args,list) or name in seen: continue
        seen.add(name)
        files=[]
        for i,arg in enumerate(args[:-1]):
            if arg in ('--native','--model','-m','--ple-gguf'): files.extend(model_files(args[i+1]))
            if arg=='--pack':
                source=read_json(Path(args[i+1])/'pack-source.json')
                for filename in source.get('files',{}):
                    if Path(filename).name!=filename: continue  # Auxiliary drafter/engram files are not the base model.
                    candidate=Path(source.get('model_dir',''))/filename
                    if candidate.exists(): files.extend(model_files(candidate))
        files=list(dict.fromkeys(files))
        size=sum(p.stat().st_size for p in files)
        choices=read_json(STRATA/'launch'/(path.stem+'.choices.json'))
        if not choices:
            for choice in (STRATA/'launch').glob('*.choices.json'):
                candidate=read_json(choice)
                linked=read_json(choice.with_name(choice.name.replace('.choices.json','.json')))
                if linked.get('model_name')==name:
                    choices=candidate; break
        family=choices.get('family') or ('deepseek41' if 'deepseek' in name else 'qwen' if 'qwen' in name else '')
        tag=choices.get('model') or path.stem.removeprefix('strata-')
        rows.append({'id':'strata:'+name,'name':name,'runtime':'Strata','parameters':None,'size':size or None,
            'quant':tag,'source':str(path),'files':[str(p) for p in files], 'family':family,'tag':tag,
            'context':choices.get('context') or arg_value(args,'--max-context',16384), 'port':conf.get('port',8080),
            'meta':{},'status':'Installed profile' if files or any(a=='--pack' and Path(args[i+1]).exists() for i,a in enumerate(args[:-1])) else 'Files missing / check profile'})
    return rows

def arg_value(args, flag, default=None):
    try: return int(args[args.index(flag)+1])
    except (ValueError,IndexError,TypeError): return default

def inventory(base, roots):
    rows=strata_inventory(); warnings=[]
    ollama_names=set()
    try:
        for model in api(base,'/api/tags').get('models',[]):
            d=model.get('details',{}); name=model['name']
            ollama_names.add(name)
            rows.append({'id':'ollama:'+name,'name':name,'runtime':'Ollama','parameters':parameter_number(d.get('parameter_size')),
                'quant':d.get('quantization_level',''), 'size':model.get('size'), 'source':base, 'files':[], 'status':'Installed', 'meta':{},'context':16384})
    except Exception as exc: warnings.append('Ollama inventory unavailable: '+str(exc))
    # Discover installed Ollama models even when the service is not running.
    stores=[HOME/'.ollama/models']
    if os.environ.get('OLLAMA_MODELS'): stores.insert(0,Path(os.environ['OLLAMA_MODELS']))
    for store in dict.fromkeys(stores):
        manifests=store/'manifests'
        if not manifests.is_dir(): continue
        for manifest in manifests.rglob('*'):
            if not manifest.is_file(): continue
            rel=manifest.relative_to(manifests).parts
            if len(rel)<4: continue
            name='/'.join(rel[2:-1])+':'+rel[-1]
            if rel[1]!='library': name=rel[1]+'/'+name
            if rel[0]!='registry.ollama.ai': name=rel[0]+'/'+name
            data=read_json(manifest); layers=data.get('layers',[])
            if not layers: continue
            if name in ollama_names:
                existing=next((r for r in rows if r['runtime']=='Ollama' and r['name']==name),None)
                if existing:
                    existing['manifest']=str(manifest)
                    existing['files']=[str(store/'blobs'/layer.get('digest','').replace(':','-')) for layer in layers if layer.get('mediaType')=='application/vnd.ollama.image.model' and (store/'blobs'/layer.get('digest','').replace(':','-')).is_file()]
                continue
            config=read_json(store/'blobs'/data.get('config',{}).get('digest','').replace(':','-'))
            files=[store/'blobs'/layer.get('digest','').replace(':','-') for layer in layers if layer.get('mediaType')=='application/vnd.ollama.image.model']
            missing=any(not p.is_file() for p in files)
            rows.append({'id':'ollama:'+name,'name':name,'runtime':'Ollama','parameters':parameter_number(config.get('model_type')),
                'quant':str(config.get('file_type','Unknown')),'size':sum(layer.get('size',0) for layer in layers),
                'source':base,'manifest':str(manifest),'files':[str(p) for p in files if p.is_file()], 'status':'Files missing' if missing else 'Installed (disk manifest)',
                'meta':{},'context':16384})
            ollama_names.add(name)
    registered={str(Path(p).resolve()).lower() for r in rows for p in r.get('files',[])}
    for root in roots:
        folder=Path(root)
        if not folder.is_dir(): continue
        for path in folder.rglob('*.gguf'):
            if str(path.resolve()).lower() in registered or any(s in path.name.lower() for s in ('mmproj','engram','dspark','draft')): continue
            if re.search(r'-\d{5}-of-\d{5}\.gguf$',path.name) and '-00001-of-' not in path.name: continue
            files=model_files(path)
            split=re.search(r'-\d{5}-of-(\d{5})\.gguf$',path.name)
            incomplete=bool(split and len(files)!=int(split[1]))
            rows.append({'id':'gguf:'+str(path),'name':path.stem,'runtime':'GGUF file','parameters':None,
                'quant':'See model metadata','size':sum(p.stat().st_size for p in files),'source':str(path),
                'files':[str(p) for p in files],'status':'Incomplete split model' if incomplete else 'File discovered; choose runtime',
                'incomplete':incomplete,'meta':{},'context':16384})
    cache=read_json(STATE/'metadata.json')
    for row in rows:
        files=row.get('files',[])
        if not files: continue
        try:
            sig='|'.join(f'{p}:{Path(p).stat().st_size}:{Path(p).stat().st_mtime_ns}' for p in files)
            key=hashlib.sha256(sig.encode()).hexdigest()
            if key not in cache:
                metas=[read_gguf(p) for p in files]
                cache[key]=dict(metas[0], tensor_parameters=sum(m['tensor_parameters'] for m in metas))
            row['meta']=cache[key]
            row['parameters']=row['meta'].get('general.parameter_count') or row['meta'].get('tensor_parameters') or parameter_number(row['meta'].get('general.size_label')) or row.get('parameters')
        except Exception as exc: warnings.append(row['name']+': metadata unavailable: '+str(exc))
    for row in rows:
        if 'embedding' in row['name'].lower(): row['status']='Embedding model (not for OpenCode chat)'
    save_json(STATE/'metadata.json',cache)
    return sorted(rows,key=lambda r:r['name'].casefold()), warnings

def default_profile(row):
    return {'context':int(row.get('context') or 16384), 'output':4096, 'temperature':0.2,'top_p':0.9,
        'parallel':1,'kv':'q4_0','vision':'off','port':int(row.get('port') or 8080),
        'reserve_gib':6, 'kv_budget_gib':4, 'runtime_base':'http://127.0.0.1:'+str(row.get('port') or 8080),
        'llama_exe':'', 'gpu_layers':-1, 'threads':max(1,(os.cpu_count() or 4)//2)}

def validate_profile(p):
    p=dict(p)
    for key,low,high in [('context',2048,2097152),('output',128,131072),('parallel',1,8),('port',1024,65535),('gpu_layers',-1,999),('threads',1,1024)]:
        p[key]=int(p[key])
        if not low<=p[key]<=high: raise ValueError(f'{key} must be between {low} and {high}')
    for key,low,high in [('temperature',0,2),('top_p',0.01,1),('reserve_gib',1,128),('kv_budget_gib',0,1024)]:
        p[key]=float(p[key])
        if not low<=p[key]<=high: raise ValueError(f'{key} must be between {low} and {high}')
    if p['output']>=p['context']: raise ValueError('Output limit must be smaller than context.')
    if p['kv'] not in ('q4_0','int8','k8v4','fp16'): raise ValueError('Invalid KV format')
    if p['vision'] not in ('off','on','cpu','gpu'): raise ValueError('Invalid vision mode')
    p['runtime_base']=local_url(p['runtime_base'])
    return p

def advice(memory, processes, reserve=6, gpu=None):
    notes=[]
    if memory['available']<reserve*GiB:
        notes.append(f'LOW SYSTEM RAM: {memory["available"]/GiB:.1f} GiB free. Save work, then stop an unused model or close memory-heavy apps.')
    if memory['commit_free']<reserve*GiB:
        notes.append(f'LOW COMMIT HEADROOM: {memory["commit_free"]/GiB:.1f} GiB remains. New allocations can fail; avoid launching another model.')
    if not gpu or not gpu.get('available'): notes.append('GPU usage unavailable. The hub cannot confirm GPU allocation headroom; check the runtime/Task Manager before a large launch.')
    elif len(gpu.get('adapters',{}))==1:
        capacity=sum(g.get('capacity') or 0 for g in gpu_hardware())
        used=sum(a['dedicated']+a['shared'] for a in gpu['adapters'].values())
        if capacity and used>capacity*.9: notes.append('GPU MEMORY PRESSURE: Windows counter usage exceeds 90% of the configured graphics capacity. Avoid another launch; stop an unused engine or restart the chosen model with smaller context. This comparison is not the runtime allocation budget.')
    oc=[r for r in processes if r['category'].startswith('OpenCode')]
    private=sum(r.get('private') or 0 for r in oc)
    if private>4*GiB: notes.append(f'OpenCode process tree uses {private/GiB:.1f} GiB private memory. Try /compact; save and restart OpenCode if memory stays high.')
    engines=sorted([r for r in processes if r['category'] in ('Ollama','Strata','Model engine')],key=lambda r:r.get('private') or 0,reverse=True)
    if len(engines)>1:
        notes.append('Review unused engines: '+', '.join(f'{r["name"]} PID {r["pid"]} ({(r.get("private") or 0)/GiB:.1f} GiB private)' for r in engines[:4])+'. Stop only ones you are no longer using.')
    return notes or ['System memory currently has headroom. This is not a guarantee that a GPU allocation will fit.']

def preflight(row,p,memory,gpu=None):
    warnings=[]; reserve=p['reserve_gib']*GiB
    if memory['available']<reserve: warnings.append('System RAM is below your reserve.')
    if memory['commit_free']<reserve: warnings.append('Commit headroom is below your reserve.')
    # File size is not exact residency, especially for Strata expert caching.
    if row.get('size') and row['size']+p['kv_budget_gib']*GiB>memory['available']-reserve:
        warnings.append('Model files plus your extra-memory allowance exceed free system RAM after reserve. GPU residency / expert caching may change actual use; this is a conservative warning, not a fit prediction.')
    maximum=next((v for k,v in row.get('meta',{}).items() if k.endswith('.context_length')),None)
    if maximum and p['context']>maximum: warnings.append(f'Context exceeds the model metadata limit ({maximum:,}).')
    if p['parallel']>1: warnings.append('Parallel requests increase memory use. Start with one when investigating allocation failures.')
    if not gpu or not gpu.get('available'): warnings.append('GPU counters unavailable: graphics-memory headroom is unknown.')
    return warnings

def ps_quote(value): return "'"+str(value).replace("'","''")+"'"

def strata_command(row,p,restart=False):
    if not row.get('family'): raise ValueError('No supported Strata family identified for this profile.')
    command=['strata','launch','--family',row['family'],'--model',row['tag'],'--context',str(p['context']),
        '--parallel',str(p['parallel']),'--kv',p['kv'],'--vision',p['vision'],'--tune','skip',
        '--access','local','--port',str(p['port']),'--no-tui']
    if restart: command.append('--restart')
    return command

def opencode_config(model,base,p):
    import hub_terminal
    return hub_terminal.config(model,base,p,HOME)

class Gateway:
    """Loopback only. Stores counts and timing, never prompt/response text."""
    def __init__(self,row,p,base,model):
        self.row=row; self.profile=dict(p); self.base=local_url(base); self.model=model
        self.records=deque(maxlen=500); self.lock=threading.Lock(); self.active=0; self.server=None
        self.warning=''; self.limit=threading.BoundedSemaphore(p['parallel'])

    def start(self):
        gateway=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args): pass
            def do_GET(self): self.relay()
            def do_POST(self): self.relay()
            def relay(self):
                if self.path.split('?')[0] not in ('/v1/models','/v1/chat/completions','/v1/completions'):
                    self.send_error(404); return
                body=None; started=time.monotonic(); first=None; usage={}; timings={}; status=0; complete=False
                acquired=False
                try:
                    if self.command=='POST':
                        length=int(self.headers.get('Content-Length','0'))
                        if not 0<length<=32*1024*1024: self.send_error(413); return
                        body=json.loads(self.rfile.read(length))
                        body['model']=gateway.model
                        body['temperature']=gateway.profile['temperature']; body['top_p']=gateway.profile['top_p']
                        requested=body.get('max_tokens',gateway.profile['output'])
                        body['max_tokens']=min(int(requested),gateway.profile['output'])
                        if body.get('stream'): body['stream_options']={**body.get('stream_options',{}),'include_usage':True}
                        # Queue requests instead of killing/pausing an active generation.
                        acquired=gateway.limit.acquire(timeout=600)
                        if not acquired: self.send_error(503,'Request queue timed out'); return
                        with gateway.lock: gateway.active+=1
                    req=Request(gateway.base+self.path, data=None if body is None else json.dumps(body).encode(),
                        headers={'Content-Type':'application/json'})
                    with HTTP.open(req,timeout=600) as response:
                        status=response.status; self.send_response(status)
                        self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
                        self.send_header('Connection','close'); self.end_headers(); self.close_connection=True
                        is_sse='text/event-stream' in response.headers.get('Content-Type','')
                        if is_sse:
                            for line in response:
                                self.wfile.write(line); self.wfile.flush()
                                if line.startswith(b'data: '):
                                    raw=line[6:].strip()
                                    if raw==b'[DONE]': complete=True; continue
                                    try: data=json.loads(raw)
                                    except ValueError: continue
                                    if data.get('usage'): usage=data['usage']
                                    if data.get('timings'): timings=data['timings']
                                    if first is None and any(c.get('delta',{}).get('content') or c.get('delta',{}).get('reasoning_content') for c in data.get('choices',[])): first=time.monotonic()
                                    if any(c.get('finish_reason') for c in data.get('choices',[])): complete=True
                        else:
                            raw=response.read(); self.wfile.write(raw)
                            try:
                                data=json.loads(raw); usage=data.get('usage',{}); timings=data.get('timings',{}); complete=True
                            except ValueError: pass
                except HTTPError as exc:
                    status=exc.code
                    try:
                        data=exc.read(); self.send_response(status); self.send_header('Content-Type','application/json')
                        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                    except OSError: pass
                except Exception as exc:
                    if not status:
                        try: self.send_error(502,'Local model backend unavailable')
                        except OSError: pass
                    gateway.warning='Request failed or client disconnected: '+type(exc).__name__
                finally:
                    if acquired:
                        with gateway.lock: gateway.active-=1
                        gateway.limit.release()
                        elapsed=time.monotonic()-started
                        out=usage.get('completion_tokens'); prompt=usage.get('prompt_tokens')
                        record={'time':time.strftime('%H:%M:%S'),'model':gateway.model,'prompt_tokens':prompt,
                            'output_tokens':out,'seconds':round(elapsed,2),'first_token_s':round(first-started,2) if first else None,
                            'effective_tps':out/elapsed if out and elapsed else None,
                            'generation_tps':timings.get('predicted_per_second'),
                            'status':status,'complete':complete,'context_limit':gateway.profile['context'],
                            'context_used':prompt+(out or 0) if prompt is not None else None}
                        with gateway.lock: gateway.records.append(record)
                        try:
                            STATE.mkdir(exist_ok=True)
                            with (STATE/'request_metrics.jsonl').open('a',encoding='utf-8') as log: log.write(json.dumps(record)+'\n')
                        except OSError: pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        return 'http://127.0.0.1:'+str(self.server.server_port)

    def close(self):
        if self.server: self.server.shutdown(); self.server.server_close()

def make_launch_script(row,p,gateway_base,backend_model,project,restart=False,managed=False,simple=True):
    p=validate_profile(p)
    project=Path(project)
    if not project.is_dir(): raise ValueError('Select an existing project folder for OpenCode.')
    import hub_terminal
    config=hub_terminal.config(backend_model,gateway_base,p,project,simple=simple,display_name=row['name'])
    lines=["$ErrorActionPreference = 'Stop'", '# Generated by Model Monitor. Review before running.',
        'Set-Location -LiteralPath '+ps_quote(project),
        '$Host.UI.RawUI.WindowTitle = '+ps_quote('OpenCode | '+project.name+' | '+row['name']),
        'Write-Host '+ps_quote('Project: '+str(project)),
        'Write-Host '+ps_quote('Model: '+row['name']+' | Context: '+str(p['context'])+' tokens')]
    if managed:
        lines.append("Write-Host 'The hub checked this model on the GPU before opening OpenCode.'")
    elif row['runtime']=='Strata':
        cmd=strata_command(row,p,restart)
        lines.append('& '+ ' '.join(ps_quote(s) for s in cmd))
        lines.append("if ($LASTEXITCODE -ne 0) { throw 'Strata did not start. Read its message above; OpenCode was not launched.' }")
    elif row['runtime']=='Ollama':
        payload={'model':backend_model,'from':row['name'],'parameters':{
            'num_ctx':p['context'],'temperature':p['temperature'],'top_p':p['top_p'],'num_predict':p['output']},'stream':False}
        lines += ["try { $null = Invoke-RestMethod -Uri "+ps_quote(local_url(row['source'])+'/api/version')+" -TimeoutSec 2 } catch {",
            '  $env:OLLAMA_HOST = '+ps_quote(local_url(row['source'])),
            '  $env:OLLAMA_NUM_PARALLEL = '+ps_quote(p['parallel']),
            "  Start-Process -FilePath (Get-Command ollama.exe -ErrorAction Stop).Source -ArgumentList 'serve' -WindowStyle Hidden",
            '  $ollamaReady = $false', '  for ($i=0; $i -lt 30; $i++) {',
            '    try { $null = Invoke-RestMethod -Uri '+ps_quote(local_url(row['source'])+'/api/version')+" -TimeoutSec 2; $ollamaReady=$true; break } catch { Start-Sleep -Seconds 1 }",
            '  }', "  if (-not $ollamaReady) { throw 'Ollama did not start.' }", '}',
            '$modelInfo = Invoke-RestMethod -Method Post -Uri '+ps_quote(local_url(row['source'])+'/api/show')+" -ContentType 'application/json' -Body "+ps_quote(json.dumps({'model':row['name']})),
            "if ($modelInfo.remote_host -or $modelInfo.remote_model) { throw 'This model points to a cloud or remote server. The hub only launches local models.' }",
            '$payload = '+ps_quote(json.dumps(payload)),
            "$null = Invoke-RestMethod -Method Post -Uri "+ps_quote(local_url(row['source'])+'/api/create')+" -ContentType 'application/json' -Body $payload -TimeoutSec 600"]
    elif row['runtime']=='GGUF file':
        exe=Path(p['llama_exe'])
        if not exe.is_file() or exe.suffix.lower()!='.exe': raise ValueError('Select your llama-server.exe for this GGUF model.')
        command=[str(exe),'-m',row['files'][0],'-c',str(p['context']),'-np',str(p['parallel']),'-ngl',str(p['gpu_layers']),
            '-t',str(p['threads']),'--host','127.0.0.1','--port',str(p['port']),'--alias',backend_model]
        # A separate engine terminal stays visible to show allocation errors and accepts Ctrl+C.
        engine='& '+' '.join(ps_quote(s) for s in command)
        encoded=__import__('base64').b64encode(engine.encode('utf-16le')).decode()
        lines.append("Start-Process powershell.exe -ArgumentList @('-NoProfile','-NoExit','-EncodedCommand',"+ps_quote(encoded)+")")
        lines.append("Write-Host 'Waiting for llama-server. Close its terminal or press Ctrl+C there to stop it.'")
    base=p['runtime_base'] if row['runtime']!='Ollama' else row['source']
    if row['runtime']=='Strata': base='http://127.0.0.1:'+str(p['port'])
    lines += ["$ready = $false", 'for ($i=0; $i -lt 600; $i++) {',
        '  try { $health = Invoke-RestMethod -Uri '+ps_quote(base+'/v1/models')+" -TimeoutSec 2; if ($health.data.Count -gt 0) { $ready=$true; break } } catch {}",
        '  Start-Sleep -Seconds 2', '}',
        "if (-not $ready) { throw 'Model did not become ready. Check the engine log before retrying.' }",
        ]
    if simple:
        directory=hub_terminal.prepare_simple_directory()
        lines += ['$env:XDG_CONFIG_HOME = '+ps_quote(directory),
            '$env:OPENCODE_CONFIG_DIR = '+ps_quote(directory/'opencode'),
            "[Environment]::SetEnvironmentVariable('OPENCODE_CONFIG', $null, 'Process')",
            "$env:OPENCODE_DISABLE_CLAUDE_CODE = '1'",
            "Write-Host 'Simple OpenCode: Build and Plan, one local model. Your original configuration is kept.'"]
    lines += ['$env:OPENCODE_CONFIG_CONTENT = '+ps_quote(json.dumps(config)),
        "Write-Host 'Memory advisor: keep Model Monitor open. Context changes apply on next model launch.'",
        '& opencode --standalone']
    return '\n'.join(lines)+'\n'
