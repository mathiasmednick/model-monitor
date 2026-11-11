"""Plain-language memory findings, safe fix choices, and portable help reports."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import re
import time
import hub_core as core
import hub_chat as chat
import hub_gpu

BROWSERS={'chrome.exe':'Google Chrome','msedge.exe':'Microsoft Edge','brave.exe':'Brave','firefox.exe':'Firefox'}
SETTING_NAMES={'context':'Memory for this conversation','output':'Longest reply','parallel':'Answers at the same time',
    'temperature':'Answer creativity','top_p':'Word choice range','kv':'Conversation memory format',
    'vision':'Picture reading','reserve_gib':'RAM kept free','kv_budget_gib':'Extra room for a model',
    'gpu_layers':'Parts kept on the graphics chip','threads':'CPU workers'}

def gb(value):return f'{value/core.GiB:.1f} GB'

def visible_name(name):return BROWSERS.get(str(name).lower(),name)

def findings(snapshot,reserve=6):
    m=snapshot['memory_bytes']; available=m['available'];total=m['total'];used=total-available
    low=available<reserve*core.GiB
    lines=[('Your system RAM is running low.' if low else 'Your system RAM is not low right now.')+
        f' {gb(available)} is free. Windows can use {gb(total)} in total. About {gb(used)} is in use.']
    live=snapshot.get('live_ollama_gpu',{})
    for model in live.get('models',[])[:3]:
        if model['state']=='no graphics memory reported':lines.append(f'RIGHT NOW: Ollama reports no graphics memory for {model["name"]}. This needs a GPU check. It does not tell us why; do not change the 32 GB system-RAM guard to try to fix it.')
        elif model.get('graphics_bytes'):lines.append(f'RIGHT NOW: Ollama reports {gb(model["graphics_bytes"])} of graphics memory for {model["name"]}. This model does have graphics-memory use; it is not limited to the regular RAM number shown at the top.')
    lines.extend(hub_gpu.explanation(snapshot.get('saved_gpu_evidence',[])[:2]))
    installed=m.get('installed_physical');reserved=m.get('not_os_usable')
    if installed and reserved and reserved>8*core.GiB:
        lines.append(f'Your PC has {gb(installed)} installed. Windows exposes {gb(total)} as regular RAM; {gb(reserved)} is reserved outside that pool. This is not evidence that the memory is broken or lost. Closing apps will not get that part back.')
        graphics=sum(g.get('capacity') or 0 for g in snapshot.get('graphics',[]))
        if graphics and abs(graphics-reserved)<2*core.GiB:
            lines.append(f'The graphics setting is {gb(graphics)}, which closely matches the reserved amount. A large graphics-memory reservation is the likely reason. Keeping that graphics allocation leaves less regular RAM for Windows. The app has not read the BIOS setting directly.')
    elif total<40*core.GiB and any(g.get('capacity',0)>64*core.GiB for g in snapshot.get('graphics',[])):
        lines.append('The main thing to check is the split between graphics memory and regular RAM. Windows has about 32 GB to work with, while your graphics setting is about 96 GB. I cannot read the installed-RAM total here, so I cannot confirm how much is reserved.')
    apps=snapshot['consumers']['top_apps']
    if apps:
        largest=apps[0]
        lines.append(f'The biggest app I can see is {visible_name(largest["name"])}: about {gb(largest["working_set"])} in RAM across {largest["processes"]} process(es).')
        if len(apps)>1:
            lines.append('Other large apps I can see: '+', '.join(visible_name(r['name'])+' '+gb(r['working_set']) for r in apps[1:5])+'.')
    runtime_bytes=sum(r.get('ram') or 0 for r in snapshot.get('model_processes',[]))
    if snapshot.get('model_processes'):lines.append(f'The AI app and model processes I can see use about {gb(runtime_bytes)} of RAM together. Shared memory may be counted more than once. This is not their graphics-memory use.')
    if m.get('kernel_nonpaged') is not None:
        lines.append('Windows and its drivers also use memory that does not appear under app names. The help report includes those readings. The app will not try to force-clear that memory.')
    visible=snapshot['consumers'].get('accessible_working_sets_sum')
    hidden=(m.get('system_process_count',0)>snapshot['consumers']['accessible_processes']+snapshot['consumers']['inaccessible_processes']+10 or
        (visible is not None and used and visible<used*.25))
    if hidden:
        lines.append('The scan cannot see enough app memory to explain all the RAM in use. It would be wrong to blame the small apps listed above. Open the desktop app normally and scan again; a restricted scan or Windows/driver memory may explain the gap.')
    if m['commit_free']<reserve*core.GiB:lines.append(f'Windows has only {gb(m["commit_free"])} of memory-allocation room left (commit headroom). This can block model loads even with free RAM. Open Memory health > Repair allocation limit to increase the disk-backed allowance while keeping your graphics reservation. Administrator approval and a restart are required. Disk-backed memory is slower than RAM; this does not add VRAM or guarantee a model will fit. Avoid starting another model until this improves.')
    lines.append('Choose a fix below. Each fix says what it will change. The app will measure memory again afterward. It will not change the BIOS or close apps on its own.')
    return '\n\n'.join(lines)

def build_fixes(snapshot,models,profiles,processes,loaded):
    choices=[]
    for row in sorted(loaded,key=lambda r:r.get('vram') or 0,reverse=True)[:4]:
        p=chat.unload_proposal(row['name'],row['base'],'This model is loaded. Stop it only if you no longer need it. Any active answer will end.',loaded)
        p['button']='Free model memory';p['summary']='Unload '+row['name']+'. Any answer using it will stop.'
        choices.append(p)
    # Exact process identities are captured; never guess whether an engine is idle.
    for row in sorted(processes.values(),key=lambda r:r.get('ram') or 0,reverse=True):
        if row.get('kind') not in ('Strata','Model engine') or not row.get('birth'):continue
        p=chat.stop_proposal(row['pid'],'This model process is using memory. Stop it only if its work is finished.',processes)
        p['button']='Stop this model';p['summary']=f'Stop {row["name"]} (ID {row["pid"]}). It shows {gb(row.get("ram") or 0)} in RAM. Active work will end.'
        choices.append(p)
        if sum(x['kind']=='stop' for x in choices)>=3:break
    for app in snapshot['consumers']['top_apps']:
        if app['name'].lower() not in BROWSERS or app['working_set']<core.GiB:continue
        members=[r for r in snapshot['consumers'].get('all_processes',[]) if r['name'].lower()==app['name'].lower() and r.get('birth')]
        if not members:continue
        choices.append({'id':chat.uuid.uuid4().hex,'kind':'close_browser','name':visible_name(app['name']),
            'members':members,'reason':'Close its browser windows after saving anything important. The browser may keep some background tasks running.',
            'summary':f'Ask {visible_name(app["name"])} to close its windows. It shows about {gb(app["working_set"])} in RAM. Save your work first.',
            'button':'Close browser windows'})
    for row in models.values():
        if row.get('incomplete') or 'Embedding model' in row['status']:continue
        before={**core.default_profile(row),**profiles.get(row['id'],{})}
        if before['context']<=8192 and before['parallel']==1:continue
        # Offer this for models with saved user profiles or a recognized running model, not every installed file.
        if row['id'] not in profiles and row['id']!=snapshot.get('selected_model_id') and not any(row['name']==r['name'] for r in loaded):continue
        changes={'context':min(before['context'],8192),'output':min(before['output'],2048),'parallel':1}
        p=chat.profile_proposal(row['id'],changes,'Use less memory on the next run. The model will remember less text, and long jobs may need a fresh chat sooner.',models,profiles)
        p['button']='Use less memory next run';p['summary']=f'{row["name"]}: remember up to {changes["context"]:,} tokens; write up to {changes["output"]:,}; answer one request at a time. Saves future settings only.'
        choices.append(p)
        if sum(x['kind']=='profile' for x in choices)>=3:break
    return choices

def close_browser_windows(members):
    """Request a normal close of verified browser windows. No force kill."""
    valid=set()
    for row in members:
        if row['name'].lower() not in BROWSERS:raise ValueError('Only supported browser windows can be closed this way.')
        h=core.native.K.OpenProcess(0x1000,False,row['pid'])
        if h:
            try:
                buf=C.create_unicode_buffer(32768);length=W.DWORD(len(buf))
                readable=core.native.K.QueryFullProcessImageNameW(h,0,buf,C.byref(length))
                if readable and Path(buf.value).name.lower()==row['name'].lower() and core.native.birth(h)==row['birth']:valid.add(row['pid'])
            finally:core.native.K.CloseHandle(h)
    if not valid:raise ValueError('Those browser processes changed or already closed. Scan again.')
    user=C.WinDLL('user32',use_last_error=True);callback_type=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=[callback_type,W.LPARAM];user.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user.IsWindowVisible.argtypes=[W.HWND];user.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    count=0
    @callback_type
    def visit(hwnd,unused):
        nonlocal count
        pid=W.DWORD();user.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value in valid and user.IsWindowVisible(hwnd) and user.PostMessageW(hwnd,0x10,0,0):count+=1
        return True
    user.EnumWindows(visit,0)
    if not count:raise ValueError('No open browser windows were found. Background tasks were left alone.')
    return count

def model_ref(model_id):return hashlib.sha256(model_id.encode()).hexdigest()[:12]

def help_report(snapshot,models,profiles,recent_requests=None):
    # Export only a small, explicit field list: no prompts, keys, command lines or local paths.
    model_rows=[]
    for row in models.values():
        p={**core.default_profile(row),**profiles.get(row['id'],{})}
        model_rows.append({'model_ref':model_ref(row['id']),'name':row['name'],'runtime':row['runtime'],
            'parameters':row.get('parameters'),'files_bytes':row.get('size'),'status':row['status'],
            'settings':{k:p[k] for k in chat.ALLOWED_PROFILE_FIELDS}})
    import hub_repair
    data={'time':snapshot['sample_time'],'memory_bytes':snapshot['memory_bytes'],'graphics':snapshot['graphics'],'guided_gpu_test':hub_repair.report_summary(),
        'gpu_readings':snapshot['gpu_counters'],'live_ollama_gpu':snapshot.get('live_ollama_gpu',{}),'saved_gpu_evidence':snapshot.get('saved_gpu_evidence',[]),
        'largest_apps':[{k:r[k] for k in ('name','processes','working_set','private')} for r in snapshot['consumers']['top_apps']],
        'process_visibility':{k:snapshot['consumers'].get(k) for k in ('accessible_processes','inaccessible_processes')},
        'model_processes':[{k:r.get(k) for k in ('name','category','ram','private')} for r in snapshot['model_processes']],
        'models':model_rows,'recent_speed':[ {k:r.get(k) for k in ('model','prompt_tokens','output_tokens','context_limit','context_used','seconds','effective_tps','generation_tps','status')} for r in (recent_requests or [])[-10:]]}
    return ('LOCAL AI HELP REPORT\nNothing is uploaded automatically. Memory sizes below are bytes; 1 GB in the app means 1,073,741,824 bytes.\n\n'+
        findings(snapshot)+'\n\nMEASURED DATA\n'+json.dumps(data,indent=2)+
        '\n\nREQUEST FOR THE OTHER AI\nExplain the likely cause in simple words. Separate measured facts from guesses. Do not claim graphics memory is free memory. If recommending a supported model-settings fix, return a JSON plan in this form:\n'+
        '{"changes":[{"model_ref":"use the exact model_ref above","settings":{"context":8192,"output":2048,"parallel":1},"reason":"Explain the benefit and tradeoff"}]}\n'+
        'Supported settings: '+', '.join(sorted(chat.ALLOWED_PROFILE_FIELDS))+'. Do not include shell commands, file paths, deletion, BIOS or driver changes. The user will review all changes before applying them. If the report cannot explain the problem, name the missing evidence.\n')

def parse_advice(text,models,profiles):
    text=text.strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I)
    data=json.loads(text)
    if not isinstance(data,dict) or set(data)!={'changes'} or not isinstance(data['changes'],list) or not 1<=len(data['changes'])<=10:
        raise ValueError('Paste a JSON fix plan with a changes list (up to 10 items).')
    by_ref={model_ref(key):key for key in models};proposals=[];seen=set()
    for item in data['changes']:
        if not isinstance(item,dict) or set(item)-{'model_ref','settings','reason'}:raise ValueError('This plan contains a change this app cannot make.')
        ref=item.get('model_ref')
        if ref not in by_ref:raise ValueError('A model in this plan is not in your current model list. Copy a fresh help report.')
        if ref in seen:raise ValueError('Use one settings change per model so the changes do not conflict.')
        seen.add(ref)
        proposals.append(chat.profile_proposal(by_ref[ref],item.get('settings'),item.get('reason','Advice pasted from another AI.'),models,profiles))
    return proposals
