"""OpenCode V2 terminal configuration for one local model and Build/Plan."""
from pathlib import Path
import json
import re
import shutil
import hub_core as core

def config_json(path):
    try:
        text=Path(path).read_text(encoding='utf-8-sig')
        # Remove JSONC comments without changing quoted URLs or escaped strings.
        text=re.sub(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/',lambda m:m[0] if m[0].startswith('"') else '',text)
        text=re.sub(r'"(?:\\.|[^"\\])*"|,\s*([}\]])',lambda m:m[0] if m[0].startswith('"') else m[1],text)
        return json.loads(text)
    except (OSError,ValueError):return {}

def custom_agent_names(project):
    names={'ask','general','explore','cluster-lead','cluster-analyst','cluster-reviewer'}
    folders=[core.HOME/'.config/opencode',core.HOME/'.opencode']
    project=Path(project).resolve()
    for parent in (project,*project.parents):
        folders.extend([parent/'.opencode',parent])
        if (parent/'.git').exists():break
    for folder in dict.fromkeys(folders):
        for dirname in ('agents','agent'):
            path=folder/dirname
            if path.is_dir():
                for file in path.rglob('*.md'):names.add(file.relative_to(path).with_suffix('').as_posix())
        for filename in ('opencode.json','opencode.jsonc','config.json'):
            data=config_json(folder/filename)
            for key in ('agents','agent'):
                if isinstance(data.get(key),dict):names.update(data[key])
    return sorted(names-{'build','plan','title','summary','compaction'})

def config(model,base,p,project,simple=True,display_name=None):
    selection='modelhub/'+model
    result={'$schema':'https://opencode.ai/config.json','model':selection,'default_agent':'build',
        'providers':{'modelhub':{'name':'Local AI Hub','package':'@opencode/ai/providers/openai-compatible',
            'settings':{'baseURL':base+'/v1'},'compatibility':{'maxTokensField':'max_tokens','streamUsage':True},
            'models':{model:{'name':display_name or model,'modelID':model,'capabilities':{'tools':True,'input':['text'],'output':['text']},
                'limit':{'context':p['context'],'output':p['output']}}}}},
        'compaction':{'auto':True,'buffer':'10%','keep':{'tokens':min(15000,max(2000,p['context']//4))}},
        'tool_output':{'max_lines':300,'max_bytes':16000},
        'agents':{name:{'model':selection} for name in ('build','plan','title','summary','compaction')},
        'policies':[{'action':'provider.use','resource':'*','effect':'deny'},{'action':'provider.use','resource':'modelhub','effect':'allow'}]}
    if simple:
        result['agents'].update({name:{'disabled':True} for name in custom_agent_names(project)})
        result['permissions']=[{'action':'subagent','resource':'*','effect':'deny'}]
        for name in ('build','plan'):result['agents'][name].update(mode='primary',disabled=False)
    return result

def prepare_simple_directory():
    destination=core.STATE/'opencode-simple';destination.mkdir(parents=True,exist_ok=True)
    (destination/'opencode').mkdir(exist_ok=True)
    backup=core.STATE/'opencode-original-backup'
    source=core.HOME/'.config/opencode'
    if not backup.exists() and source.is_dir():
        backup.mkdir()
        for name in ('opencode.json','opencode.jsonc','cli.json','tui.json'):
            path=source/name
            if path.is_file():shutil.copy2(path,backup/name)
        for dirname in ('agents','agent'):
            if (source/dirname).is_dir():shutil.copytree(source/dirname,backup/dirname)
    return destination
