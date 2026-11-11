"""Honest, GPU-first labels for this unified-memory PC."""
import hub_core as core

def gb(n):return 'not reported' if n is None else f'{n/core.GiB:.1f} GB'

def memory_warning(memory,reserve=6):
    if not memory:return ''
    warnings=[]
    if memory['available']<reserve*core.GiB:warnings.append('LOW WINDOWS RAM')
    if memory['commit_free']<reserve*core.GiB:
        warnings.append('LOW ALLOCATION ROOM: '+gb(memory['commit_free'])+' left — open Memory health')
    return '⚠ '+' · '.join(warnings)+' — ' if warnings else ''

def memory_header(hardware,memory,loaded,gpu,installed=None):
    capacity=sum(g.get('capacity') or 0 for g in hardware) or None
    readings={}
    for model in loaded:
        if model.get('vram') is not None:readings[(model.get('base'),model['name'])]=model['vram']
    model_use=gb(sum(readings.values())) if readings else 'not reported'
    primary=f'Graphics memory setting: {gb(capacity)}  •  Tracked Ollama model GPU use: {model_use}  •  Free GPU memory: not verified'
    if gpu.get('available'):
        dedicated=sum(v.get('dedicated',0) for v in gpu.get('adapters',{}).values())
        shared=sum(v.get('shared',0) for v in gpu.get('adapters',{}).values())
        primary=f'Graphics memory setting: {gb(capacity)}  •  Windows GPU use: {gb(dedicated)} dedicated + {gb(shared)} shared  •  Models: {model_use}'
    is_unified=any('8060' in g['name'] for g in hardware)
    secondary=('Unified RAM installed: ' if is_unified else 'Physical RAM installed: ')+gb(installed)
    if memory:secondary+=f'  •  Regular Windows RAM: {gb(memory["available"])} free of {gb(memory["total"])} usable'
    if is_unified:secondary+='  •  CPU and GPU share one physical memory pool'
    return primary,secondary
