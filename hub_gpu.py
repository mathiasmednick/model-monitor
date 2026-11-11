"""Extract GPU facts from known local runtime logs, without copying log text."""
from datetime import datetime, timezone
from pathlib import Path
import re
import hub_core as core

def live_ollama_usage(base):
    try:
        result=[]
        for model in core.api(base,'/api/ps',timeout=2).get('models',[]):
            vram=model.get('size_vram');size=model.get('size')
            state='unknown'
            if vram==0 and size:state='no graphics memory reported'
            elif vram is not None and vram>0:state='graphics memory in use'
            result.append({'name':model['name'],'graphics_bytes':vram,'model_memory_bytes':size,'state':state})
        return {'reachable':True,'models':result}
    except Exception:return {'reachable':False,'models':[]}

def parse_runtime_log(text,runtime):
    result={'runtime':runtime,'gpu_seen':False}
    backends=re.findall(r'loaded\s+(ROCm|Vulkan|CUDA)\s+backend',text,re.I)
    if backends:result.update(gpu_seen=True,backend=backends[-1])
    if re.search(r'Device\s+\d+:.*8060S|ROCm0.*8060S|Vulkan\d.*8060S',text,re.I):result['gpu_seen']=True;result['device']='AMD Radeon 8060S'
    allocations=re.findall(r'allocating\s+([\d.]+)\s+MiB on device[^\r\n]*out of memory',text,re.I)
    if allocations:result['failed_gpu_allocation_gib']=float(allocations[-1])/1024
    if not allocations:
        buffers=re.findall(r'failed to allocate\s+(?:ROCm|Vulkan|CUDA)\d*\s+buffer of size\s+(\d+)',text,re.I)
        if buffers:result['failed_gpu_allocation_gib']=int(buffers[-1])/core.GiB
    cache=re.findall(r'ExpertCache:\s*cudaMalloc\(([\d.]+) GiB\) failed:[^\r\n]*Windows has ([\d.]+) GiB of commit left',text,re.I)
    if cache:
        result.update(gpu_seen=True,failed_gpu_allocation_gib=float(cache[-1][0]),commit_free_at_failure_gib=float(cache[-1][1]),cache_reduced='trying a smaller expert cache' in text)
    guards=re.findall(r'memory guard: limit ([\d.]+) GiB \(source: meminfo\)',text)
    if guards:result['system_ram_guard_gib']=float(guards[-1])
    free=re.findall(r'(\d+) MiB of VRAM free with everything loaded',text)
    if free:result.update(gpu_seen=True,gpu_free_at_saved_load_gib=int(free[-1])/1024)
    return result

def saved_gpu_evidence():
    files=[(core.HOME/'AppData/Local/Ollama/server.log','Ollama')]
    candidates=list((core.STRATA/'launch').glob('*.engine.log'))
    try:candidates=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[:3]
    except OSError:candidates=[]
    files.extend((p,'Strata') for p in candidates)
    results=[]
    for path,runtime in files:
        try:
            with path.open('rb') as stream:
                stream.seek(max(0,path.stat().st_size-262144));text=stream.read().decode('utf-8','replace')
            facts=parse_runtime_log(text,runtime)
            if facts['gpu_seen'] or facts.get('failed_gpu_allocation_gib'):
                facts['source']=path.name;facts['log_modified_utc']=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
                facts['scope']='saved log; not a current GPU-usage reading';results.append(facts)
        except OSError:pass
    return results

def explanation(evidence):
    lines=[]
    for item in evidence:
        runtime=item['runtime'];text=f'Saved {runtime} run'
        if item.get('gpu_seen'):text+=': the model runner saw the graphics chip'
        if item.get('failed_gpu_allocation_gib'):
            text+=f', but a {item["failed_gpu_allocation_gib"]:.1f} GB graphics-memory request failed'
        text+='.'
        if item.get('system_ram_guard_gib'):
            text+=f' Its {item["system_ram_guard_gib"]:.1f} GB guard is for regular Windows RAM. That number is not a cap on graphics memory. Do not change it to 96 GB just to match the graphics setting.'
        if item.get('commit_free_at_failure_gib'):
            text+=f' Windows reported {item["commit_free_at_failure_gib"]:.1f} GB of memory room backed by RAM and disk at that failure. This is a clue about the failed request, not proof of its exact cause.'
        text+=' This is saved evidence, not proof of what a model is using right now.'
        lines.append(text)
    return lines
