"""Model Monitor / Local AI Control Hub. Run with pythonw.exe."""
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import hub_core as core
import hub_chat as chat
import hub_help as helpdesk
import hub_repair as repair
import hub_display as display
import hub_projects as projects

class Hub:
    def __init__(self,root):
        self.root=root; self.events=queue.Queue(); self.models={}; self.processes={}; self.gateways={}
        self.memory={}; self.gpu={}; self.loaded=[]; self.hist=deque(maxlen=100); self.metrics=[]
        self.busy_inventory=False; self.busy_live=False; self.busy_gpu=False; self.closing=False
        self.chat_busy=False; self.chat_discovering=False; self.chat_targets={}; self.chat_history=[]; self.chat_proposals={}; self.chat_cancel=threading.Event()
        self.last_snapshot=None;self.fix_before=None
        self.hardware=core.gpu_hardware();self.installed_memory=None
        self.repair_busy=False;self.repair_cancel=threading.Event();self.repair_view=None;self.managed_sessions=[]
        self.settings=core.read_json(core.STATE/'settings.json',{'roots':[str(core.HOME/'AI-Model-Library'),str(core.HOME/'.lmstudio/models'),str(core.HOME/'.cache/lm-studio/models')], 'ollama':'http://127.0.0.1:11434','project':str(core.HOME)})
        self.profiles=core.read_json(core.STATE/'profiles.json')
        root.title('Model Monitor — Local AI Control Hub'); root.geometry('1280x880'); root.minsize(1000,720)
        icon=core.HERE/'model_monitor.ico'
        if icon.exists(): root.iconbitmap(str(icon))
        style=ttk.Style(root); style.theme_use('clam')
        root.configure(background='#f5f5f7')
        style.configure('.',font=('Segoe UI',10),background='#f5f5f7',foreground='#1d1d1f')
        style.configure('TFrame',background='#f5f5f7')
        style.configure('TLabel',background='#f5f5f7',foreground='#1d1d1f')
        style.configure('Muted.TLabel',foreground='#6e6e73')
        style.configure('TButton',padding=(14,9),background='#e8e8ed',borderwidth=0)
        style.map('TButton',background=[('active','#dcdce2')])
        style.configure('Primary.TButton',background='#007aff',foreground='white',font=('Segoe UI',10,'bold'))
        style.map('Primary.TButton',background=[('active','#0062cc'),('disabled','#c5d7ed')],foreground=[('disabled','#ffffff')])
        style.configure('TEntry',padding=7,fieldbackground='white',bordercolor='#dedee3',lightcolor='#dedee3',darkcolor='#dedee3')
        style.configure('TCombobox',padding=6,fieldbackground='white',arrowsize=12)
        style.configure('Treeview',rowheight=38,font=('Segoe UI',10),background='white',fieldbackground='white',borderwidth=0)
        style.map('Treeview',background=[('selected','#e2edff')],foreground=[('selected','#003f88')])
        style.configure('Treeview.Heading',font=('Segoe UI',9,'bold'),background='#eeeeF2',relief='flat',padding=(8,10))
        style.configure('TLabelframe',borderwidth=0,background='#f5f5f7')
        style.configure('TLabelframe.Label',foreground='#6e6e73',background='#f5f5f7')
        style.layout('Hub.TNotebook', [('Notebook.client', {'sticky':'nswe'})])
        style.layout('Hub.TNotebook.Tab', [])
        top=ttk.Frame(root,padding=(18,12)); top.pack(fill='x')
        ttk.Label(top,text='Model Monitor',font=('Segoe UI',23,'bold')).pack(side='left')
        ttk.Label(top,text='Your local AI, in one place',style='Muted.TLabel',padding=(18,9)).pack(side='left')
        ttk.Button(top,text='Refresh',command=self.refresh_all).pack(side='right')
        self.banner=tk.StringVar(value='Reading graphics memory…')
        ttk.Label(root,textvariable=self.banner,font=('Segoe UI',11,'bold'),wraplength=1200,padding=(18,4)).pack(fill='x')
        self.ram_banner=tk.StringVar(value='Windows RAM is shown separately from model GPU use.')
        ttk.Label(root,textvariable=self.ram_banner,wraplength=1200,padding=(18,0,18,2)).pack(fill='x')
        body=ttk.Frame(root);body.pack(fill='both',expand=True,padx=18,pady=14)
        sidebar=tk.Frame(body,background='#eaeaf0',width=174);sidebar.pack(side='left',fill='y',padx=(0,20));sidebar.pack_propagate(False)
        tk.Label(sidebar,text='WORKSPACE',background='#eaeaf0',foreground='#777780',font=('Segoe UI',8,'bold'),anchor='w',padx=14,pady=18).pack(fill='x')
        self.tabs=ttk.Notebook(body,style='Hub.TNotebook'); self.tabs.pack(side='left',fill='both',expand=True)
        self.library=ttk.Frame(self.tabs,padding=12); self.live=ttk.Frame(self.tabs,padding=12)
        self.performance=ttk.Frame(self.tabs,padding=12); self.guard=ttk.Frame(self.tabs,padding=12)
        self.chat_tab=ttk.Frame(self.tabs,padding=12)
        pages=[(self.library,'Models'),(self.chat_tab,'Assistant'),(self.live,'Running'),(self.performance,'Performance'),(self.guard,'Memory health')]
        self.navigation={}
        for frame,label in pages:
            self.tabs.add(frame,text=label)
            button=tk.Button(sidebar,text=label,anchor='w',padx=14,pady=12,relief='flat',borderwidth=0,font=('Segoe UI',11),background='#eaeaf0',activebackground='#dce7f8',command=lambda f=frame:self.tabs.select(f))
            button.pack(fill='x',padx=8,pady=2);self.navigation[str(frame)]=button
        tk.Label(sidebar,text='PRIVATE BY DEFAULT\nRuns on your computer',justify='left',anchor='w',background='#eaeaf0',foreground='#777780',font=('Segoe UI',8),padx=14,pady=20).pack(side='bottom',fill='x')
        def highlight(event=None):
            for page,button in self.navigation.items():
                active=page==self.tabs.select();button.configure(background='#dce7f8' if active else '#eaeaf0',foreground='#0056b3' if active else '#494950',font=('Segoe UI',11,'bold' if active else 'normal'))
        self.tabs.bind('<<NotebookTabChanged>>',highlight);highlight()
        self.build_library(); self.build_live(); self.build_performance(); self.build_guard(); self.build_chat()
        self.status=tk.StringVar(value='Ready')
        ttk.Label(root,textvariable=self.status,wraplength=1200,padding=(18,0,18,10)).pack(side='bottom',fill='x',before=body)
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(100,self.refresh_all); root.after(100,self.poll); root.after(2500,self.tick)
        root.after(1000,self.refresh_gpu)
        root.after(5000,self.check_managed_models)

    def check_managed_models(self):
        items=[item for item in self.managed_sessions if not item['warned']]
        def work():
            for item in items:
                session=item['session'];reason=None
                if session.process.poll() is not None:reason='The tested model server stopped.'
                elif item['row']['runtime']=='Ollama':
                    try:
                        models=core.api(session.base,'/api/ps',timeout=2).get('models',[])
                        model=next((r for r in models if r['name']==session.model),None)
                        if model and model.get('size') and not model.get('size_vram'):reason='Ollama now reports model memory with no graphics-memory use.'
                    except Exception:pass
                if reason:self.events.put(('managed_alert',(item,reason)))
        if items:threading.Thread(target=work,daemon=True).start()
        if not self.closing:self.root.after(5000,self.check_managed_models)

    def build_chat(self):
        bar=ttk.Frame(self.chat_tab);bar.pack(fill='x',pady=(0,8))
        ttk.Label(bar,text='Local assistant:').pack(side='left')
        self.chat_model=tk.StringVar(value='No running chat model detected')
        self.chat_models=ttk.Combobox(bar,textvariable=self.chat_model,state='readonly',width=65)
        self.chat_models.pack(side='left',padx=8)
        self.chat_models.bind('<<ComboboxSelected>>',self.change_chat_model)
        ttk.Button(bar,text='Find running models',command=self.refresh_chat_models).pack(side='left')
        ttk.Button(bar,text='New conversation',command=self.clear_chat).pack(side='right')
        self.chat_status=tk.StringVar(value='LOCAL ONLY — no ChatGPT/OpenAI calls or allowance used. AI chat uses an already-running local model.')
        ttk.Label(self.chat_tab,textvariable=self.chat_status,wraplength=1150).pack(anchor='w',pady=(0,5))
        self.chat_split=ttk.Panedwindow(self.chat_tab,orient='vertical');self.chat_split.pack(fill='both',expand=True)
        area=ttk.Frame(self.chat_split);self.chat_split.add(area,weight=4)
        ttk.Label(area,text='Conversation').pack(anchor='w')
        self.chat_text=tk.Text(area,wrap='word',font=('Segoe UI',11),padx=18,pady=14,state='disabled',height=10,relief='flat',background='white',foreground='#1d1d1f')
        sb=ttk.Scrollbar(area,orient='vertical',command=self.chat_text.yview);sb.pack(side='right',fill='y')
        self.chat_text.configure(yscrollcommand=sb.set);self.chat_text.pack(fill='both',expand=True)
        self.chat_text.tag_configure('user',foreground='#225590',font=('Segoe UI',11,'bold'))
        self.chat_text.tag_configure('assistant',foreground='#143d2c')
        quick=ttk.Frame(self.chat_tab);quick.pack(fill='x',pady=5,before=self.chat_split)
        ttk.Button(quick,text='Explain my memory',command=self.ram_check).pack(side='left')
        ttk.Button(quick,text='Copy help report',command=self.copy_help_report).pack(side='left',padx=5)
        ttk.Button(quick,text='Paste advice from another AI',command=self.paste_advice).pack(side='left')
        composer=ttk.Frame(self.chat_split);self.chat_split.add(composer,weight=1)
        ttk.Label(composer,text='Your message — drag the divider above to resize').pack(anchor='w')
        self.chat_input=tk.Text(composer,height=6,wrap='word',font=('Segoe UI',11),relief='flat',padx=14,pady=12,background='white');self.chat_input.pack(fill='both',expand=True)
        self.chat_input.bind('<Control-Return>',lambda e:(self.send_chat(),'break')[-1])
        actions=ttk.Frame(self.chat_tab);actions.pack(fill='x',pady=6)
        ttk.Label(actions,text='Ctrl+Enter sends • The assistant shares your running model and may compete with coding tasks.').pack(side='left')
        self.chat_send=ttk.Button(actions,text='Send',style='Primary.TButton',command=self.send_chat);self.chat_send.pack(side='right')
        ttk.Button(actions,text='Cancel reply',command=self.cancel_chat).pack(side='right',padx=7)
        proposals=ttk.LabelFrame(self.chat_tab,text='Ways to help — choose one, then click Do this fix',padding=7);proposals.pack(fill='x')
        buttons=ttk.Frame(proposals);buttons.pack(fill='x',pady=(0,5))
        ttk.Button(buttons,text='Do this fix…',command=self.apply_proposal).pack(side='right')
        ttk.Button(buttons,text='Not now',command=self.dismiss_proposal).pack(side='right',padx=7)
        self.proposal_tree=self.tree(proposals,{'name':'Model or app','change':'What this fix does','state':'Status'},
            {'name':260,'change':720,'state':110},select='browse',height=2)
        self.proposal_tree.bind('<<TreeviewSelect>>',self.proposal_details)
        self.proposal_detail=tk.StringVar(value='I will explain each fix before doing it. Nothing changes on its own.')
        ttk.Label(proposals,textvariable=self.proposal_detail,wraplength=1100).pack(anchor='w',pady=4)
        self.proposal_panel=proposals;self.proposal_panel.pack_forget()
        self.chat_status.set('Local chat only. Type below, or click Explain my memory. Nothing changes without your approval.')

    def append_chat(self,role,text):
        self.chat_text.configure(state='normal');self.chat_text.insert('end',role+'\n','user' if role=='You' else 'assistant')
        self.chat_text.insert('end',str(text)+'\n\n');self.chat_text.configure(state='disabled');self.chat_text.see('end')

    def copy_help_report(self):
        if self.chat_busy:
            self.chat_status.set('Please wait for this check to finish, then click Copy help report.');return
        self.copy_after_check=True;self.ram_check('Make a fresh help report that I can copy to another AI.')

    def paste_advice(self):
        window=tk.Toplevel(self.root);window.title('Use advice from another AI');window.geometry('850x560')
        ttk.Label(window,text='Paste the fix plan from the other AI here. I will check it and show each change before doing anything.\nA JSON plan from the help report works without a running model.',wraplength=800,padding=10).pack(anchor='w')
        text=tk.Text(window,wrap='word',font=('Segoe UI',11));text.pack(fill='both',expand=True,padx=10)
        buttons=ttk.Frame(window,padding=10);buttons.pack(fill='x')
        def review():
            try:
                plans=helpdesk.parse_advice(text.get('1.0','end'),self.models,self.profiles)
                for p in plans:self.add_proposal(p)
                self.append_chat('Hub',f'I checked the plan. {len(plans)} fix(es) are ready to review below. Nothing has changed.');window.destroy()
            except Exception as exc:messagebox.showerror('This plan needs a change',str(exc),parent=window)
        def ask_local():
            if not self.chat_targets.get(self.chat_model.get()):
                messagebox.showinfo('A local model is needed','Start your usual local model and click Find running models, or ask the other AI for the JSON plan shown in your help report.',parent=window);return
            advice=text.get('1.0','end').strip()
            if not advice:return
            window.destroy();self.chat_suggestion('Read this advice from another AI. Check it against my live PC readings. Explain it in simple words and propose only supported fixes for me to review. Do not run commands.\n\n'+advice)
        ttk.Button(buttons,text='Check JSON fix plan',command=review).pack(side='right')
        ttk.Button(buttons,text='Ask my local AI to read it',command=ask_local).pack(side='right',padx=7)

    def add_proposal(self,p):
        self.proposal_panel.pack(fill='x',pady=(8,0))
        if p['kind']=='profile':p['summary']='Next run: '+'; '.join(helpdesk.SETTING_NAMES.get(k,k)+f': {d["before"]} → {d["after"]}' for k,d in p['changes'].items())
        p['state']='Pending';self.chat_proposals[p['id']]=p
        self.proposal_tree.insert('','end',iid=p['id'],values=(p['name'],p['summary'],'Pending'))

    def check_after_fix(self):
        if self.closing:return
        if self.chat_busy:self.root.after(1500,self.check_after_fix);return
        self.ram_check('Check how much memory is free after that fix.')

    def clear_chat(self):
        if self.chat_busy: return
        self.chat_history=[];self.chat_text.configure(state='normal');self.chat_text.delete('1.0','end');self.chat_text.configure(state='disabled')
        self.append_chat('Hub','New conversation. Pending changes remain below until applied or dismissed.')

    def change_chat_model(self,event=None):
        if self.chat_busy: return
        self.chat_history=[]
        self.append_chat('Hub','Assistant changed to '+self.chat_model.get()+'. Conversation context was reset.')

    def refresh_chat_models(self):
        if self.chat_discovering: return
        self.chat_discovering=True;self.chat_status.set('Looking for resident local chat models; no models will be launched…')
        base=self.ollama.get();models={key:dict(value) for key,value in self.models.items()}
        for key,p in self.profiles.items():
            if key in models: models[key]['port']=p.get('port',models[key].get('port',8080))
        def work():
            try:self.events.put(('chat_models',chat.discover_targets(base,models)))
            except Exception as exc:self.events.put(('chat_models_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def ram_check(self,prompt=None):
        if self.chat_busy:return
        self.chat_busy=True;self.chat_send.configure(state='disabled');self.chat_cancel=threading.Event()
        self.append_chat('You',prompt or 'Why is my RAM low?');self.chat_status.set('Checking what is using memory and finding fixes…')
        models=dict(self.models);profiles={k:dict(v) for k,v in self.profiles.items()};gpu=dict(self.gpu);ollama_base=self.ollama.get()
        selection=self.model_tree.selection();selected_id=selection[0] if selection else None
        def work():
            try:
                snapshot=chat.diagnostic_snapshot(models,profiles,gpu,ollama=ollama_base)
                snapshot['selected_model_id']=selected_id
                processes={f'p:{r["pid"]}':dict(r,kind=r['category']) for r in snapshot['model_processes']}
                try:
                    base=core.local_url(self.settings['ollama'])
                    loaded=[{'name':r['name'],'base':base,'vram':r.get('size_vram')} for r in core.api(base,'/api/ps',timeout=2).get('models',[])]
                except Exception:loaded=[]
                fixes=helpdesk.build_fixes(snapshot,models,profiles,processes,loaded)
                self.events.put(('chat_reply',{'answer':helpdesk.findings(snapshot),'proposals':fixes,'prompt':None,'diagnostic':True,'snapshot':snapshot}))
            except Exception as exc:self.events.put(('chat_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def chat_suggestion(self,text):
        if self.chat_busy:return
        self.chat_input.delete('1.0','end');self.chat_input.insert('1.0',text);self.send_chat()

    def send_chat(self):
        if self.chat_busy:return
        prompt=self.chat_input.get('1.0','end').strip()
        if not prompt:return
        if len(prompt)>12000:messagebox.showerror('Chat','Please keep each question under 12,000 characters.');return
        lower=prompt.lower()
        if any(word in lower for word in ('ram','memory')) and any(word in lower for word in ('why','low','using','available','how much','free','check','explain')):
            self.chat_input.delete('1.0','end');self.ram_check(prompt);return
        target=self.chat_targets.get(self.chat_model.get())
        if not target:
            if any(word in prompt.lower() for word in ('ram','memory','15gb','15 gb')):
                self.chat_input.delete('1.0','end');self.ram_check(prompt)
            else:self.append_chat('Hub','No running local chat model is available. Start your usual local model, then click Find running models. You can use the RAM check without loading a model.')
            return
        self.chat_busy=True;self.chat_send.configure(state='disabled');self.chat_models.configure(state='disabled');self.chat_cancel=threading.Event()
        self.append_chat('You',prompt);self.chat_input.delete('1.0','end');self.chat_status.set('Reading live diagnostics, then asking '+target['model']+'…')
        models={k:dict(v) for k,v in self.models.items()};profiles={k:dict(v) for k,v in self.profiles.items()}
        processes={k:dict(v) for k,v in self.processes.items()};loaded=[dict(r) for r in self.loaded]
        history=[dict(m) for m in self.chat_history];gpu=dict(self.gpu);cancel=self.chat_cancel;ollama_base=self.ollama.get()
        def work():
            try:
                snapshot=chat.diagnostic_snapshot(models,profiles,gpu,ollama=ollama_base)
                answer,proposals=chat.run_turn(dict(target),history,prompt,snapshot,models,profiles,processes,loaded,cancel)
                self.events.put(('chat_reply',{'answer':answer,'proposals':proposals,'prompt':prompt,'diagnostic':False,'snapshot':snapshot}))
            except Exception as exc:self.events.put(('chat_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def cancel_chat(self):
        if not self.chat_busy:return
        self.chat_cancel.set();self.chat_status.set('Cancellation requested. Waiting for the current backend request to return; nothing will be applied.')

    def proposal_details(self,event=None):
        keys=self.proposal_tree.selection()
        if keys:
            p=self.chat_proposals[keys[0]]
            self.proposal_detail.set(p['reason']+' '+('Used next time you start this model. It will not free RAM right now.' if p['kind']=='profile' else 'Save important work first. This may end work that is still running.'))

    def dismiss_proposal(self):
        for key in self.proposal_tree.selection():
            p=self.chat_proposals[key]
            if p.get('state')=='Pending':p['state']='Dismissed';self.proposal_tree.set(key,'state','Dismissed')

    def apply_proposal(self):
        if self.chat_busy:return
        keys=self.proposal_tree.selection()
        if not keys:return
        p=self.chat_proposals[keys[0]]
        if p.get('state')!='Pending':return
        try:
            if p['kind']=='gpu_test':
                if p['model_id'] not in self.models:raise ValueError('Refresh the model list and ask again.')
                self.filter.set('');self.render_models();self.model_tree.selection_set(p['model_id']);self.choose_model();self.tabs.select(self.library)
                self.test_gpu_setup(optimize=p.get('optimize',False));p['state']='Opened';self.proposal_tree.set(p['id'],'state','Opened')
            elif p['kind']=='profile':
                row=self.models.get(p['model_id'])
                if not row:raise ValueError('The model is no longer in the inventory.')
                current={**core.default_profile(row),**self.profiles.get(row['id'],{})}
                if current!=p['before']:raise ValueError('This profile changed since the proposal. Ask for a fresh proposal to avoid overwriting your edits.')
                if not messagebox.askyesno('Save these model settings?',p['name']+'\n\n'+p['summary']+'\n\nThese settings will be used next time you start this model. Your running model will not change. Save?'):return
                updated=dict(self.profiles);updated[row['id']]=core.validate_profile(p['after'])
                core.save_json(core.STATE/'profiles.json',updated);self.profiles=updated
                p['state']='Applied';self.proposal_tree.set(p['id'],'state','Applied');self.choose_model()
                self.append_chat('Hub','Applied saved settings for '+p['name']+': '+p['summary']+'. Running models were not changed.')
            else:
                if not messagebox.askyesno('Do this fix?',p['name']+'\n\n'+p['summary']+'\n\n'+p['reason']+'\n\nSave important work first. Continue?'):return
                self.fix_before=core.system_memory()['available']
                p['state']='Applying';self.proposal_tree.set(p['id'],'state','Applying')
                def work():
                    try:
                        if p['kind']=='close_browser':helpdesk.close_browser_windows(p['members'])
                        elif p['kind']=='stop':core.stop_process(p['process'])
                        else:
                            running=core.api(p['base'],'/api/ps').get('models',[])
                            if not any(r['name']==p['name'] for r in running):raise ValueError('The model is no longer loaded; refresh before acting.')
                            core.api(p['base'],'/api/generate',{'model':p['name'],'keep_alive':0,'stream':False},timeout=30)
                        self.events.put(('chat_action',(p['id'],None)))
                    except Exception as exc:self.events.put(('chat_action',(p['id'],str(exc))))
                threading.Thread(target=work,daemon=True).start()
        except Exception as exc:messagebox.showerror('Could not apply proposal',str(exc))

    def tree(self,parent,columns,widths,select='extended',height=8):
        area=ttk.Frame(parent); area.pack(fill='both',expand=True)
        tree=ttk.Treeview(area,columns=tuple(columns),show='headings',selectmode=select,height=height)
        for key,title in columns.items(): tree.heading(key,text=title); tree.column(key,width=widths.get(key,100),minwidth=55)
        sb=ttk.Scrollbar(area,orient='vertical',command=tree.yview); sb.pack(side='right',fill='y')
        tree.configure(yscrollcommand=sb.set); tree.pack(fill='both',expand=True)
        return tree

    def build_library(self):
        heading=ttk.Frame(self.library);heading.pack(fill='x',pady=(0,18))
        ttk.Label(heading,text='Models',font=('Segoe UI',26,'bold')).pack(side='left')
        ttk.Button(heading,text='Add folder',command=self.add_folder).pack(side='right')
        self.filter=tk.StringVar();ttk.Entry(heading,textvariable=self.filter,width=26).pack(side='right',padx=(6,12))
        ttk.Label(heading,text='Search',style='Muted.TLabel').pack(side='right')
        self.filter.trace_add('write',lambda *_:self.render_models())
        ttk.Label(self.library,text='Choose a model. Find its fastest setup. Start coding.',style='Muted.TLabel').pack(anchor='w',pady=(0,14))
        self.project=tk.StringVar(value=self.settings.get('project',str(core.HOME)))
        projectbar=ttk.Frame(self.library);projectbar.pack(fill='x',pady=(0,12))
        ttk.Label(projectbar,text='Project',font=('Segoe UI',10,'bold')).pack(side='left',padx=(0,10))
        self.project_picker=ttk.Combobox(projectbar,textvariable=self.project,state='readonly',values=self.settings.get('recent_projects',[self.project.get()]))
        self.project_picker.pack(side='left',fill='x',expand=True)
        self.project_picker.bind('<<ComboboxSelected>>',lambda e:self.remember_project())
        ttk.Button(projectbar,text='Choose folder…',command=self.pick_project).pack(side='left',padx=(8,0))
        ttk.Button(projectbar,text='Open folder',command=self.open_project_folder).pack(side='left',padx=(8,0))
        listing=ttk.Frame(self.library);listing.pack(fill='both',expand=True)
        self.model_tree=self.tree(listing,{'name':'Name','parameters':'Parameters','runtime':'Runner','quant':'Format','size':'On disk','state':'Status'},
            {'name':270,'parameters':95,'runtime':85,'quant':105,'size':85,'state':145},select='browse',height=5)
        self.sort_key='name';self.sort_desc=False
        for key in ('name','parameters','runtime','quant','size'):self.model_tree.heading(key,command=lambda k=key:self.sort(k))
        self.model_tree.bind('<<TreeviewSelect>>',self.choose_model)
        ttk.Label(self.library,text='Click Name or Parameters to sort. B = billion parameters.',style='Muted.TLabel').pack(anchor='w',pady=(7,18))

        card=ttk.Frame(self.library,padding=(0,8));card.pack(fill='x')
        self.selected_label=tk.StringVar(value='Select a model to get started')
        ttk.Label(card,textvariable=self.selected_label,font=('Segoe UI',13,'bold'),wraplength=850).pack(anchor='w')
        self.recipe_label=tk.StringVar(value='Your tested setup will appear here.')
        ttk.Label(card,textvariable=self.recipe_label,style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=(7,18))
        self.optimization_goals={'32K–64K context':65536,'32K context':32768,'8K–16K context':16384}
        goal=self.settings.get('optimization_context',65536)
        self.optimization_goal=tk.StringVar(value=next((k for k,v in self.optimization_goals.items() if v==goal),'32K–64K context'))
        goalbar=ttk.Frame(card);goalbar.pack(fill='x')
        ttk.Label(goalbar,text='Fastest setup within').pack(side='left')
        ttk.Combobox(goalbar,textvariable=self.optimization_goal,values=tuple(self.optimization_goals),state='readonly',width=23).pack(side='left',padx=10)
        # Migrate the old 30-tok/s default; the user intended that as a floor.
        if self.settings.get('speed_objective_version',0)<2 and self.settings.get('target_tps',30)==30:
            self.settings['target_tps']=50
        self.settings['speed_objective_version']=2
        self.speed_min=tk.StringVar(value=str(self.settings.get('minimum_tps',25)))
        self.speed_target=tk.StringVar(value=str(self.settings.get('target_tps',50)))
        self.speed_caption=tk.StringVar()
        def caption(*_):
            self.speed_caption.set('As fast as possible · ideally '+self.speed_target.get()+'+ tok/s · '+self.speed_min.get()+' tok/s minimum')
        self.speed_min.trace_add('write',caption);self.speed_target.trace_add('write',caption);caption()
        ttk.Label(card,textvariable=self.speed_caption,style='Muted.TLabel').pack(anchor='w',pady=(10,16))
        actions=ttk.Frame(card);actions.pack(fill='x',pady=(0,8))
        ttk.Button(actions,text='Optimize model',style='Primary.TButton',command=lambda:self.test_gpu_setup(optimize=True)).pack(side='left')
        ttk.Button(actions,text='Model settings…',command=self.show_model_settings).pack(side='left',padx=9)
        ttk.Button(actions,text='Start in OpenCode',style='Primary.TButton',command=self.launch).pack(side='right')
        ttk.Label(card,text='Optimize compares supported setups. Speed depends on the model and your workload.',style='Muted.TLabel',wraplength=850).pack(anchor='w',pady=(8,0))

        # Keep detailed controls in a separate window, away from the everyday flow.
        self.settings_window=tk.Toplevel(self.root);self.settings_window.withdraw()
        self.settings_window.title('Model settings');self.settings_window.geometry('850x700')
        self.settings_window.protocol('WM_DELETE_WINDOW',self.settings_window.withdraw)
        scroll=ttk.Scrollbar(self.settings_window,orient='vertical');scroll.pack(side='right',fill='y')
        canvas=tk.Canvas(self.settings_window,highlightthickness=0,background='#f5f5f7',yscrollcommand=scroll.set)
        canvas.pack(fill='both',expand=True);scroll.configure(command=canvas.yview)
        box=ttk.Frame(canvas,padding=24);content=canvas.create_window((0,0),window=box,anchor='nw')
        box.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(content,width=e.width))
        self.settings_window.bind('<MouseWheel>',lambda e:canvas.yview_scroll(int(-e.delta/120),'units'))
        ttk.Label(box,text='Model settings',font=('Segoe UI',22,'bold')).grid(row=0,column=0,columnspan=8,sticky='w',pady=(0,6))
        ttk.Label(box,textvariable=self.selected_label,style='Muted.TLabel',wraplength=780).grid(row=1,column=0,columnspan=8,sticky='w',pady=(0,18))
        self.fields={};self.advanced_widgets=[]
        definitions=[('context','Context (tokens)',16384),('output','Reply limit (tokens)',4096),('parallel','Answers at once',1),('temperature','Creativity (0–2)',.2),
            ('top_p','Word choice (top P)',.9),('kv','Strata memory format','q4_0'),('vision','Strata pictures','off'),('port','Server port',8080),
            ('reserve_gib','Keep RAM free (GB)',6),('kv_budget_gib','Extra model room (GB)',4),('gpu_layers','GPU layers (-1 = all)',-1),('threads','CPU workers',8)]
        for i,(key,label,default) in enumerate(definitions):
            r=2+(i//4)*2;c=(i%4)*2
            lab=ttk.Label(box,text=label);lab.grid(row=r,column=c,columnspan=2,sticky='w',padx=(0,12))
            var=tk.StringVar(value=str(default));self.fields[key]=var
            choices={'kv':('q4_0','int8','k8v4','fp16'),'vision':('off','on','cpu','gpu')}
            widget=ttk.Combobox(box,textvariable=var,values=choices[key],state='readonly',width=15) if key in choices else ttk.Entry(box,textvariable=var,width=17)
            widget.grid(row=r+1,column=c,columnspan=2,sticky='we',padx=(0,12),pady=(4,12))
            if i>=4:self.advanced_widgets.extend([lab,widget])
        for col in range(8):box.columnconfigure(col,weight=1)
        paths=ttk.Frame(box);paths.grid(row=8,column=0,columnspan=8,sticky='we',pady=10)
        self.runtime_base=tk.StringVar(value='http://127.0.0.1:8080');self.llama_exe=tk.StringVar()
        self.ollama=tk.StringVar(value=self.settings['ollama'])
        for r,label,var,command in [(0,'Project folder',self.project,self.pick_project),(1,'llama-server.exe',self.llama_exe,self.pick_llama),(2,'Ollama address',self.ollama,None)]:
            ttk.Label(paths,text=label).grid(row=r,column=0,sticky='w',pady=5)
            ttk.Entry(paths,textvariable=var).grid(row=r,column=1,sticky='we',padx=8,pady=5)
            if command:ttk.Button(paths,text='Choose…',command=command).grid(row=r,column=2)
        paths.columnconfigure(1,weight=1);self.advanced_widgets.extend(paths.grid_slaves(row=1)+paths.grid_slaves(row=2))
        speeds=ttk.Frame(box);speeds.grid(row=9,column=0,columnspan=8,sticky='we',pady=8)
        ttk.Label(speeds,text='Minimum tok/s').pack(side='left');ttk.Entry(speeds,textvariable=self.speed_min,width=6).pack(side='left',padx=(8,20))
        ttk.Label(speeds,text='Ideal tok/s').pack(side='left');ttk.Entry(speeds,textvariable=self.speed_target,width=6).pack(side='left',padx=8)
        ttk.Label(speeds,text='No speed cap',style='Muted.TLabel').pack(side='left',padx=8)
        self.advanced=tk.BooleanVar(value=False)
        ttk.Checkbutton(box,text='Advanced settings',variable=self.advanced,command=self.toggle_advanced).grid(row=10,column=0,columnspan=8,sticky='w',pady=8)
        self.simple_opencode=tk.BooleanVar(value=self.settings.get('simple_opencode',True))
        ttk.Checkbutton(box,text='Simple OpenCode: Build and Plan with this local model',variable=self.simple_opencode).grid(row=11,column=0,columnspan=8,sticky='w',pady=5)
        tools=ttk.Frame(box);tools.grid(row=12,column=0,columnspan=8,sticky='we',pady=16)
        for label,command in [('Use less memory',self.smaller),('Start steps',self.preview),('Test GPU',self.test_gpu_setup),('Undo optimization',self.undo_gpu_setup)]:
            ttk.Button(tools,text=label,command=command).pack(side='left',padx=(0,6))
        footer=ttk.Frame(box);footer.grid(row=13,column=0,columnspan=8,sticky='we',pady=10)
        ttk.Button(footer,text='Done',command=self.settings_window.withdraw).pack(side='right')
        ttk.Button(footer,text='Save settings',style='Primary.TButton',command=self.save_profile).pack(side='right',padx=8)
        ttk.Label(footer,text='Saved settings apply to your next hub launch.',style='Muted.TLabel').pack(side='left')
        self.toggle_advanced()

    def show_model_settings(self):
        try:self.selected()
        except ValueError:
            messagebox.showinfo('Choose a model','Select a model in the list first.');return
        self.settings_window.deiconify();self.settings_window.lift()

    def toggle_advanced(self):
        for widget in self.advanced_widgets:
            if self.advanced.get():widget.grid()
            else:widget.grid_remove()

    def confirm_optimization(self,row,goal,minimum,target):
        window=tk.Toplevel(self.root);window.withdraw();window.title('Optimize model')
        window.transient(self.root);window.resizable(False,False)
        box=ttk.Frame(window,padding=28);box.pack(fill='both',expand=True)
        ttk.Label(box,text='Find your fastest setup',font=('Segoe UI',20,'bold')).pack(anchor='w')
        ttk.Label(box,text=row['name'],style='Muted.TLabel',wraplength=510).pack(anchor='w',pady=(6,22))
        low=min(goal,32768) if goal>=32768 else min(goal,8192)
        for title,value in [('Priority','Maximum measured speed'),('Ideally',f'{target:g}+ tokens/sec · no speed cap'),('Minimum',f'{minimum:g} tokens/sec'),('Context',f'{low:,}–{goal:,} tokens')]:
            line=ttk.Frame(box);line.pack(fill='x',pady=5)
            ttk.Label(line,text=title,width=12,style='Muted.TLabel').pack(side='left')
            ttk.Label(line,text=value,font=('Segoe UI',11,'bold')).pack(side='left')
        ttk.Label(box,text='Compares supported GPU routes and context sizes. Speed varies by model and task. Allow several minutes per route.',wraplength=510,style='Muted.TLabel').pack(anchor='w',pady=(20,10))
        ttk.Label(box,text='Close unused models first. Saves only a verified setup above your minimums; otherwise you choose. Undo is available.',wraplength=510).pack(anchor='w',pady=(0,22))
        result=[False]
        def finish(accepted=False):result[0]=accepted;window.destroy()
        actions=ttk.Frame(box);actions.pack(fill='x')
        ttk.Button(actions,text='Optimize',style='Primary.TButton',command=lambda:finish(True)).pack(side='right')
        ttk.Button(actions,text='Cancel',command=finish).pack(side='right',padx=8)
        window.protocol('WM_DELETE_WINDOW',finish);window.bind('<Escape>',lambda e:finish())
        window.update_idletasks();x=self.root.winfo_rootx()+(self.root.winfo_width()-window.winfo_reqwidth())//2;y=self.root.winfo_rooty()+100
        window.geometry(f'+{max(0,x)}+{max(0,y)}');window.deiconify();window.grab_set();self.root.wait_window(window)
        return result[0]

    def test_gpu_setup(self,optimize=False):
        if self.repair_busy:return
        try:
            row=dict(self.selected());p=self.profile()
            goal,minimum,target=self.optimization_preferences()
            if row.get('incomplete') or 'missing' in row['status'].lower() or 'Embedding' in row['status']:raise ValueError('This model is incomplete or is not a chat model.')
            if optimize:
                text=f'Find the fastest tested setup for {row["name"]}.\n\nSpeed: as fast as possible. {target:g}+ tokens/sec is an aspiration, not a cap.\nMinimum: {minimum:g} tokens/sec.\nContext: compare supported sizes up to {goal:,} tokens.\n\nTests may take several minutes per route. Close unused models first. A setup saves automatically only after GPU, context and minimum-speed checks pass. Slower or smaller results need your approval. You can Undo afterward.'
            else:
                text=f'Test {row["name"]} on your GPU?\n\nSave important work first. Close unused models to leave room. The test loads this model and can take several minutes. It may try more than one GPU route and an 8,192-token setting if the current size fails. It will not stop your existing models or change Windows, drivers or the BIOS.\n\nOnly a passing setup can be saved. Continue?'
            if optimize:
                if not self.confirm_optimization(row,goal,minimum,target):return
            elif not messagebox.askyesno('Test this model?',text):return
            if optimize:self.settings.update(optimization_context=goal,minimum_tps=minimum,target_tps=target);self.persist_settings()
            self.repair_busy=True;self.repair_cancel=threading.Event()
            window=tk.Toplevel(self.root);window.title('Optimize model' if optimize else 'Find a working GPU setup');window.geometry('820x480')
            status=tk.StringVar(value='Starting the test…');ttk.Label(window,textvariable=status,wraplength=760,padding=15,font=('Segoe UI',12)).pack(fill='x')
            detail=tk.Text(window,wrap='word',height=12,font=('Segoe UI',10));detail.pack(fill='both',expand=True,padx=15)
            buttons=ttk.Frame(window,padding=15);buttons.pack(fill='x')
            cancel=ttk.Button(buttons,text='Stop test',command=self.repair_cancel.set);cancel.pack(side='right')
            save=ttk.Button(buttons,text='Save passing setup',state='disabled');save.pack(side='right',padx=8)
            self.repair_view={'window':window,'status':status,'detail':detail,'save':save,'cancel':cancel,'row':row,'optimize':optimize}
            window.protocol('WM_DELETE_WINDOW',self.repair_cancel.set)
            def work():
                try:
                    runner=repair.optimize if optimize else repair.run_trials
                    result=runner(row,p,self.repair_cancel,lambda message:self.events.put(('repair_progress',message)),**({'goal_context':goal,'minimum_tps':minimum,'target_tps':target} if optimize else {}))
                    self.events.put(('repair_result',(row,result,None)))
                except Exception as exc:self.events.put(('repair_result',(row,None,str(exc))))
            threading.Thread(target=work,daemon=True).start()
        except Exception as exc:messagebox.showerror('GPU test',str(exc))

    def save_gpu_setup(self,row,result,confirm=True):
        try:
            performance=result.get('performance_goal');extra=''
            if performance:
                speed=performance['measured_writing_tps'];extra='\nWriting speed: '+(f'{speed:.2f} tokens/sec' if speed else 'not verified')
                if not performance['minimum_met']:extra+='\nThis DOES NOT meet your verified writing-speed minimum. Save it anyway?'
                if not performance.get('context_goal_met',True):extra+='\nThis is below your minimum context goal. Save the smaller context anyway?'
            if confirm and not messagebox.askyesno('Keep this working setup?',f'{row["name"]}\n\nGPU route: {result["choice"]["backend"]}\nRemembered text: {result["choice"]["profile"]["context"]:,} tokens\nAnswers at once: 1'+extra+'\n\nThis becomes the setup for this model when started from this hub. Undo restores the previous choice. Save?'):return False
            if repair.fingerprint(row,result['choice']['profile'])!=result['fingerprint']:raise ValueError('The model, driver or runtime changed since the test. Test again before saving.')
            repair.save_recipe(row,result,self.profiles.get(row['id']))
            self.profiles[row['id']]=result['choice']['profile'];core.save_json(core.STATE/'profiles.json',self.profiles)
            self.choose_model();self.status.set('Saved a passing GPU setup for '+row['name']+'. Other programs keep their own settings.')
            self.repair_view['save'].configure(state='disabled')
            return True
        except Exception as exc:messagebox.showerror('Could not save the setup',str(exc));return False

    def undo_gpu_setup(self):
        try:
            row=self.selected()
            if not messagebox.askyesno('Undo the saved GPU choice?','Restore the previous GPU choice for '+row['name']+'? Running models will not change.'):return
            previous_profile=repair.undo_recipe(row['id'])
            if previous_profile is None:self.profiles.pop(row['id'],None)
            else:self.profiles[row['id']]=previous_profile
            core.save_json(core.STATE/'profiles.json',self.profiles);self.choose_model();self.status.set('Previous GPU choice restored. Running models were not changed.')
        except Exception as exc:messagebox.showerror('Undo',str(exc))

    def launch_tested(self,row,p,recipe):
        if self.repair_busy:raise ValueError('Wait for the GPU test to finish first.')
        recheck=repair.recipe_state(row,p,recipe)!='Test passed'
        if any(item['row']['id']==row['id'] and item['session'].process.poll() is None for item in self.managed_sessions):raise ValueError('This model already has a hub server running. Use its existing OpenCode terminal, or stop that model in What is running? before starting it again.')
        if recheck:
            details='\n'.join('• '+line for line in repair.recipe_changes(row,p,recipe))
            title='Recheck and start?'
            text=f'{row["name"]}\n\n{details}\n\nRecheck the saved {recipe["choice"]["backend"]} route with your current model settings? This checks GPU use, answers, writing speed and a large input for contexts of 32K or more. It can take several minutes.\n\nIf it passes, the hub updates the verified setup and opens OpenCode. A failure stops only this test server. Your previous saved setup remains. Other models are not stopped. Keep the hub open.'
        else:
            title='Start with the tested setup?'
            text=f'Start {row["name"]} using its saved GPU setup?\n\nThe hub will check two short answers and GPU use again before it opens OpenCode. Other models are not stopped. Keep this app open.'
        text+='\n\nProject folder: '+self.project.get()
        if not messagebox.askyesno(title,text):return
        project=self.project.get()
        if not Path(project).is_dir():raise ValueError('Choose an existing project folder.')
        self.remember_project()
        self.repair_busy=True;self.repair_cancel=threading.Event();self.status.set('Starting and checking your saved GPU setup…')
        def work():
            session=None
            try:
                choice=dict(recipe['choice'],profile=p)
                result,session=repair.run_choice(row,choice,self.repair_cancel,lambda m:self.events.put(('repair_progress',m)),keep=True,benchmark=recheck,context_probe=recheck and p['context']>=32768)
                if recheck:result['refresh_saved_setup']=True
                self.events.put(('verified_started',(row,result,session,project)))
            except Exception as exc:
                if session:session.stop()
                self.events.put(('verified_start_error',(row['id'],str(exc))))
        threading.Thread(target=work,daemon=True).start()

    def finish_verified_start(self,data):
        row,result,session,project=data;gateway=None
        try:
            if self.closing:session.stop();return
            if result.pop('refresh_saved_setup',False):
                benchmark=result.get('benchmark',{})
                speed=benchmark.get('generation_tps') if benchmark.get('comparable') else None
                minimum=float(self.speed_min.get())
                if speed is None or speed<minimum:
                    measured=f'{speed:.1f} tokens/sec' if speed is not None else 'not verified'
                    if not messagebox.askyesno('Below your speed minimum',f'GPU and answer checks passed, but writing speed is {measured}. Your minimum is {minimum:g} tokens/sec. Save this setup and open OpenCode anyway?'):
                        session.stop();self.status.set('Start cancelled. Your previous saved setup is unchanged.');return
                repair.save_recipe(row,result,self.profiles.get(row['id']))
                self.profiles[row['id']]=dict(result['choice']['profile'])
                core.save_json(core.STATE/'profiles.json',self.profiles)
                self.choose_model()
            p=dict(result['choice']['profile'],port=session.port,runtime_base=session.base)
            managed_row=dict(row,source=session.base)
            gateway=core.Gateway(managed_row,p,session.base,session.model);base=gateway.start()
            script=core.make_launch_script(managed_row,p,base,session.model,project,managed=True,simple=self.simple_opencode.get())
            path=core.STATE/('launch-'+str(gateway.server.server_port)+'.ps1');path.write_text(script,encoding='utf-8-sig')
            subprocess.Popen(['powershell.exe','-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File',str(path)],creationflags=subprocess.CREATE_NEW_CONSOLE,cwd=project)
            self.gateways[base]=gateway;self.managed_sessions.append({'row':row,'session':session,'warned':False})
            self.status.set('The model answered and GPU use passed the check. OpenCode is opening.')
        except Exception as exc:
            if gateway:gateway.close()
            session.stop();messagebox.showerror('Start failed',str(exc))

    def build_live(self):
        actions=ttk.Frame(self.live); actions.pack(fill='x',pady=(0,8))
        ttk.Label(actions,text='Shift+click selects a range; Ctrl+click adds/removes. Right-click to stop selected.').pack(side='left')
        ttk.Button(actions,text='Stop selected',command=self.stop_selected).pack(side='right')
        self.process_tree=self.tree(self.live,{'name':'Model / process','kind':'Type','pid':'PID','ram':'Working set RAM','private':'Private memory','vram':'Ollama VRAM'},
            {'name':360,'kind':170,'pid':75,'ram':150,'private':150,'vram':150},height=12)
        self.process_tree.bind('<Button-3>',self.process_menu)
        self.process_tree.bind('<Control-a>',lambda e:(self.process_tree.selection_set(self.process_tree.get_children()),'break')[-1])
        self.process_tree.bind('<<TreeviewSelect>>',self.process_details)
        self.menu=tk.Menu(self.root,tearoff=False); self.menu.add_command(label='Stop selected…',command=self.stop_selected)
        self.proc_detail=tk.Text(self.live,height=7,wrap='word',font=('Consolas',9)); self.proc_detail.pack(fill='x',pady=10); self.proc_detail.configure(state='disabled')
        ttk.Label(self.live,text='Process RAM and Ollama VRAM measure different things. OpenCode children include its tools and language servers.\nStopping a process interrupts its work. Unloading a model does not delete its files. Apps may reload it later.',wraplength=1150).pack(anchor='w')

    def build_performance(self):
        self.perf_summary=tk.StringVar(value='Waiting for process telemetry')
        ttk.Label(self.performance,textvariable=self.perf_summary,font=('Segoe UI',12,'bold'),wraplength=1150).pack(anchor='w',pady=8)
        self.chart=tk.Canvas(self.performance,height=145,background='#142238',highlightthickness=0); self.chart.pack(fill='x',pady=6)
        self.chart.bind('<Configure>',lambda e:self.draw_chart())
        ttk.Label(self.performance,text='System RAM used (blue) and OpenCode private memory (green), GiB. Rolling samples while the hub is open.').pack(anchor='w')
        ttk.Label(self.performance,text='Requests through this hub',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(14,7))
        self.request_tree=self.tree(self.performance,{'time':'Finished','model':'Model','prompt':'Prompt tokens','output':'Output tokens','window':'Context used','ttft':'First token sec','effective':'Effective tok/s','generation':'Engine tok/s','result':'HTTP / result'},
            {'time':70,'model':220,'prompt':100,'output':90,'window':140,'ttft':100,'effective':100,'generation':100,'result':100},height=8)
        ttk.Label(self.performance,text='Effective tok/s = output tokens ÷ total request time, including queue/prefill. Engine tok/s is shown only when the backend reports it.\nPrompt tokens measure context load, not OpenCode RAM. Compare trends; this view cannot prove what caused a slowdown.\nOnly terminals launched through this hub send request metrics here. Existing terminals are still included in process-memory readings.\nThe metrics log contains counts/timings, never prompt text. Keep the hub open while using its terminals.',wraplength=1150).pack(anchor='w',pady=10)

    def build_guard(self):
        actions=ttk.Frame(self.guard);actions.pack(fill='x',pady=(0,10))
        ttk.Button(actions,text='Repair allocation limit…',style='Primary.TButton',command=self.repair_allocation).pack(side='left')
        ttk.Button(actions,text='Undo allocation repair…',command=lambda:self.repair_allocation(undo=True)).pack(side='left',padx=8)
        ttk.Label(self.guard,text='Memory warnings — you choose what to do',font=('Segoe UI',16,'bold')).pack(anchor='w',pady=5)
        self.hardware_label=tk.StringVar()
        ttk.Label(self.guard,textvariable=self.hardware_label,wraplength=1150).pack(anchor='w',pady=10)
        self.advisor=tk.Text(self.guard,wrap='word',font=('Segoe UI',12),height=12); self.advisor.pack(fill='both',expand=True); self.advisor.configure(state='disabled')
        self.gpu_label=tk.StringVar(value='Checking Windows GPU counters…')
        ttk.Label(self.guard,textvariable=self.gpu_label,wraplength=1150).pack(anchor='w',pady=10)
        ttk.Label(self.guard,text='Nothing stops on its own. Open Ask for help and click Explain my memory to see named apps and fix choices.\nA paused model usually keeps its memory. To free that memory, you may need to stop the model, then start it again.\nUse one large model and one answer at a time. Optimize model can test a 64K or 32K context goal. Keep at least 6 GB of regular RAM free.\nNo app can promise that every model will fit. The help report lets another AI review the facts when more help is needed.',wraplength=1150).pack(anchor='w')

    def repair_allocation(self,undo=False):
        title='Undo allocation repair' if undo else 'Repair allocation limit'
        text=('Restore the page-file setting saved before the repair?' if undo else
            'Keep your graphics allocation. Set the C: page file to 128 GB initially, with a 192 GB maximum. This adds disk-backed memory allowance; it does not add RAM or VRAM, and cannot guarantee model speed or fit. The old setting is saved for Undo. At least 144 GB of free disk space is required before applying.')
        if not messagebox.askyesno(title,text+'\n\nWindows will ask for administrator approval. Review the result in the repair window. Restart Windows yourself after saving your work. Continue?'):return
        path="'"+str(core.HERE/'repair_allocation.ps1').replace("'","''")+"'"
        command='& ([scriptblock]::Create([IO.File]::ReadAllText('+path+'))) -ScriptPath '+path+(' -Undo' if undo else '')
        args=['powershell.exe','-NoProfile','-NoExit','-Command',command]
        subprocess.Popen(args,creationflags=subprocess.CREATE_NEW_CONSOLE,cwd=str(core.HERE))
        self.status.set('Repair opened. Approve the Windows administrator prompt, then read its result. Restart is not automatic.')

    def sort(self,key):
        self.sort_desc=not self.sort_desc if self.sort_key==key else False; self.sort_key=key; self.render_models()

    def render_models(self):
        selected=self.model_tree.selection(); text=self.filter.get().casefold()
        rows=[r for r in self.models.values() if text in (r['name']+' '+r['runtime']+' '+r['quant']).casefold()]
        known=[r for r in rows if r.get(self.sort_key) is not None]; unknown=[r for r in rows if r.get(self.sort_key) is None]
        known.sort(key=lambda r:r[self.sort_key].casefold() if isinstance(r[self.sort_key],str) else r[self.sort_key],reverse=self.sort_desc)
        ordered=known+unknown
        for key in self.model_tree.get_children():
            if key not in {r['id'] for r in ordered}: self.model_tree.delete(key)
        for index,r in enumerate(ordered):
            values=(r['name'],self.params(r.get('parameters')),r['runtime'],r['quant'],self.gib(r.get('size')),r['status'])
            if self.model_tree.exists(r['id']): self.model_tree.item(r['id'],values=values)
            else: self.model_tree.insert('','end',iid=r['id'],values=values)
            self.model_tree.move(r['id'],'',index)
        if selected and self.model_tree.exists(selected[0]): self.model_tree.selection_set(selected[0])

    @staticmethod
    def params(n): return 'Unknown' if not n else (f'{n/1e9:.2f}B' if n>=1e9 else f'{n/1e6:.1f}M')
    @staticmethod
    def gib(n): return 'Not known' if n is None else f'{n/core.GiB:.1f} GB'

    def selected(self):
        keys=self.model_tree.selection()
        if not keys: raise ValueError('Select a model first.')
        return self.models[keys[0]]

    def choose_model(self,event=None):
        try: row=self.selected()
        except ValueError: return
        p={**core.default_profile(row),**self.profiles.get(row['id'],{})}
        for key,var in self.fields.items(): var.set(str(p[key]))
        self.runtime_base.set(p['runtime_base']); self.llama_exe.set(p['llama_exe'])
        if row['runtime']=='GGUF file':self.advanced.set(True);self.toggle_advanced()
        self.selected_label.set(row['name']+' · '+self.params(row.get('parameters'))+' · '+row['runtime'])
        recipe=core.read_json(core.STATE/'verified_recipes.json').get(row['id'])
        try:self.recipe_label.set('GPU setup: '+repair.recipe_state(row,p,recipe)+'. Saved setups are checked again at every hub start.')
        except Exception:self.recipe_label.set('GPU setup: could not check files. Test again.')

    def profile(self):
        p={key:var.get() for key,var in self.fields.items()}
        p.update(runtime_base=self.runtime_base.get(),llama_exe=self.llama_exe.get())
        p['runtime_base']='http://127.0.0.1:'+str(p['port'])
        return core.validate_profile(p)

    def save_profile(self):
        try:
            row=self.selected(); p=self.profile(); self.profiles[row['id']]=p
            core.save_json(core.STATE/'profiles.json',self.profiles); self.persist_settings()
            self.status.set('Saved settings for '+row['name']+'. Running models were not changed.')
        except Exception as exc: messagebox.showerror('Profile',str(exc))

    def smaller(self):
        try:
            self.selected(); context=max(4096,int(self.fields['context'].get())//2)
            self.fields['context'].set(str(context)); self.fields['output'].set(str(min(4096,context//4)))
            self.fields['parallel'].set('1'); self.fields['vision'].set('off')
            self.status.set('Smaller settings prepared. Save or launch when ready; current work is unchanged.')
        except Exception as exc: messagebox.showerror('Settings',str(exc))

    def pick_project(self):
        path=filedialog.askdirectory(title='OpenCode project folder',initialdir=self.project.get())
        if path:self.project.set(path);self.remember_project()

    def remember_project(self):
        try:
            path,recent=projects.remember(self.project.get(),self.settings.get('recent_projects',[]))
            self.project.set(path);self.settings.update(project=path,recent_projects=recent)
            self.project_picker.configure(values=recent)
            core.save_json(core.STATE/'settings.json',self.settings)
            self.status.set('Project selected: '+path+'. New terminals use this folder; existing terminals keep their project.')
        except (ValueError,OSError) as exc:messagebox.showerror('Project folder',str(exc))

    def open_project_folder(self):
        try:
            path,_=projects.remember(self.project.get());os.startfile(path)
        except (ValueError,OSError) as exc:messagebox.showerror('Project folder',str(exc))

    def pick_llama(self):
        path=filedialog.askopenfilename(title='Select llama-server.exe',filetypes=[('Executable','*.exe')])
        if path: self.llama_exe.set(path)

    def add_folder(self):
        path=filedialog.askdirectory(title='Folder containing local model files')
        if path and path not in self.settings['roots']:
            self.settings['roots'].append(path); self.persist_settings(); self.refresh_inventory()

    def persist_settings(self):
        goal,minimum,target=self.optimization_preferences()
        project,recent=projects.remember(self.project.get(),self.settings.get('recent_projects',[]))
        self.settings.update(ollama=core.local_url(self.ollama.get()),project=project,recent_projects=recent,simple_opencode=self.simple_opencode.get(),optimization_context=goal,minimum_tps=minimum,target_tps=target)
        core.save_json(core.STATE/'settings.json',self.settings)

    def optimization_preferences(self):
        goal=self.optimization_goals[self.optimization_goal.get()]
        minimum=float(self.speed_min.get());target=float(self.speed_target.get())
        if not math.isfinite(minimum) or not math.isfinite(target) or not 1<=minimum<=target<=10000:
            raise ValueError('Use a positive speed minimum and an aspiration at least as high.')
        return goal,minimum,target

    def backend_model(self,row,p):
        if row['runtime']=='Ollama':
            digest=hashlib.sha256(json.dumps([row['name'],p],sort_keys=True).encode()).hexdigest()[:12]
            return 'modelhub-'+digest+':latest'
        return row['name']

    def preview(self):
        try:
            row=self.selected(); p=self.profile()
            script=core.make_launch_script(row,p,'http://127.0.0.1:PORT-ASSIGNED-ON-LAUNCH',self.backend_model(row,p),self.project.get(),simple=self.simple_opencode.get())
            window=tk.Toplevel(self.root); window.title('Review launch command'); window.geometry('1050x650')
            ttk.Label(window,text='Preview only. The live gateway address is assigned when you click Launch.',padding=10).pack(anchor='w')
            text=tk.Text(window,wrap='word',font=('Consolas',10)); text.pack(fill='both',expand=True); text.insert('1.0',script); text.configure(state='disabled')
        except Exception as exc: messagebox.showerror('Launch preview',str(exc))

    def launch(self):
        try:
            row=self.selected(); p=self.profile(); memory=core.system_memory()
            recipe=core.read_json(core.STATE/'verified_recipes.json').get(row['id'])
            if recipe:return self.launch_tested(dict(row),p,recipe)
            if 'Embedding model' in row['status']: raise ValueError('This is an embedding model, not a chat/tool-calling model for OpenCode. You can still monitor and stop it in the Running tab.')
            if 'missing' in row['status'].lower() or row.get('incomplete'): raise ValueError('Model files are incomplete or missing. Repair the installation before launching.')
            warnings=core.preflight(row,p,memory,self.gpu)
            if row['runtime']=='GGUF file' and p['parallel']>1:
                warnings.append('llama.cpp divides total context across parallel slots; use parallel=1 for the full context configured here.')
            # Inspect existing server options before requesting any restart.
            restart=False
            if row['runtime']=='Strata':
                try:
                    h=core.api('http://127.0.0.1:'+str(p['port']),'/health')
                    if not messagebox.askyesno('Restart the model server?',f'Port {p["port"]} already serves {h.get("model","a model")}.\n\nApplying all saved settings requires a restart and stops active answers on this server. Restart it?'): return
                    restart=True
                except (OSError,ValueError): pass
            explanation='\n\n'.join(warnings) if warnings else 'System memory has headroom. Model fit is not guaranteed.'
            explanation+=f'\n\nLaunch {row["name"]} with {p["context"]:,} context tokens and OpenCode in:\n{self.project.get()}?\n\nKeep this hub open for the terminal connection and metrics.'
            if not messagebox.askyesno('Review memory and launch',explanation): return
            self.persist_settings(); self.profiles[row['id']]=p; core.save_json(core.STATE/'profiles.json',self.profiles)
            model=self.backend_model(row,p)
            base=row['source'] if row['runtime']=='Ollama' else 'http://127.0.0.1:'+str(p['port'])
            gateway=core.Gateway(row,p,base,model); address=gateway.start()
            try:
                script=core.make_launch_script(row,p,address,model,self.project.get(),restart,simple=self.simple_opencode.get())
                path=core.STATE/('launch-'+str(gateway.server.server_port)+'.ps1')
                path.write_text(script,encoding='utf-8-sig')
                # This is the visible interactive terminal the user explicitly requested.
                subprocess.Popen(['powershell.exe','-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File',str(path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,cwd=self.project.get())
            except Exception: gateway.close(); raise
            self.gateways[address]=gateway
            self.status.set('Terminal opened. Follow its progress; large models can take several minutes. No existing OpenCode configuration was overwritten.')
        except Exception as exc: messagebox.showerror('Launch',str(exc))

    def refresh_all(self): self.refresh_inventory(); self.refresh_live()

    def refresh_inventory(self):
        if self.busy_inventory: return
        try: base=core.local_url(self.ollama.get())
        except ValueError as exc: self.status.set(str(exc)); return
        self.busy_inventory=True; self.status.set('Scanning installed profiles and model metadata (weights are not loaded)…')
        roots=list(self.settings['roots'])
        def work():
            try: self.events.put(('inventory',core.inventory(base,roots)))
            except Exception as exc: self.events.put(('inventory_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def refresh_live(self):
        if self.busy_live: return
        self.busy_live=True; base=self.ollama.get()
        bases=[base]+[item['session'].base for item in self.managed_sessions if item['row']['runtime']=='Ollama' and item['session'].process.poll() is None]
        def work():
            try:
                memory=core.system_memory(); processes=core.process_inventory(); loaded=[]; errors=[]
                for address in bases:
                    try:
                        for item in core.api(address,'/api/ps',timeout=2).get('models',[]):
                            loaded.append({'id':'ollama:'+address+':'+item['name'],'kind':'Ollama loaded model','name':item['name'],
                                'vram':item.get('size_vram'),'base':address,'pid':'','ram':None,'private':None})
                    except Exception:errors.append('Ollama offline or unavailable')
                self.events.put(('live',(memory,processes,loaded,errors)))
            except Exception as exc: self.events.put(('live_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def refresh_gpu(self):
        if not self.busy_gpu:
            self.busy_gpu=True
            def work():
                try:installed=chat.memory_details().get('installed_physical')
                except Exception:installed=None
                self.events.put(('gpu',dict(core.gpu_usage(),installed_physical=installed)))
            threading.Thread(target=work,daemon=True).start()
        if not self.closing: self.root.after(20000,self.refresh_gpu)

    def render_live(self,data):
        self.memory,processes,self.loaded,errors=data
        self.processes={f'p:{p["pid"]}:{p["birth"]}':dict(p,kind=p['category']) for p in processes}
        self.processes.update({p['id']:p for p in self.loaded})
        selected=self.process_tree.selection()
        for key in self.process_tree.get_children():
            if key not in self.processes: self.process_tree.delete(key)
        for key,p in sorted(self.processes.items(),key=lambda kv:kv[1]['name'].lower()):
            values=(p['name'],p['kind'],p['pid'],self.gib(p.get('ram')),self.gib(p.get('private')),self.gib(p.get('vram')))
            if self.process_tree.exists(key): self.process_tree.item(key,values=values)
            else: self.process_tree.insert('','end',iid=key,values=values)
        self.process_tree.selection_set([k for k in selected if k in self.processes])
        oc=[p for p in processes if p['category'].startswith('OpenCode')]
        private=sum(p.get('private') or 0 for p in oc); ram=sum(p.get('ram') or 0 for p in oc)
        m=self.memory
        self.update_memory_header()
        self.perf_summary.set(f'OpenCode + children: {len(oc)} process(es)  •  Private memory {self.gib(private)}  •  Summed working sets {self.gib(ram)}')
        self.hist.append(((m['total']-m['available'])/core.GiB,private/core.GiB)); self.draw_chart()
        reserve=6
        try: reserve=float(self.fields['reserve_gib'].get())
        except ValueError: pass
        notes=core.advice(m,processes,reserve,self.gpu)
        if m['available']<reserve*core.GiB or m['commit_free']<reserve*core.GiB:
            self.tabs.tab(self.guard,text='⚠ Memory warnings')
        else: self.tabs.tab(self.guard,text='Memory warnings')
        self.advisor.configure(state='normal'); self.advisor.delete('1.0','end')
        self.advisor.insert('1.0','\n\n'.join('• '+note for note in notes)); self.advisor.configure(state='disabled')
        hardware=core.gpu_hardware()
        self.hardware_label.set('\n'.join(g['name']+' — Windows graphics-memory setting: '+self.gib(g['capacity']) for g in hardware)+
            '\nWindows system RAM: '+self.gib(m['total'])+'. Graphics capacity is not current free memory or a guaranteed allocation budget.')
        self.render_requests()

    def update_memory_header(self):
        primary,secondary=display.memory_header(self.hardware,self.memory,self.loaded,self.gpu,self.installed_memory)
        try:reserve=float(self.fields['reserve_gib'].get())
        except (AttributeError,ValueError):reserve=6
        self.banner.set(primary);self.ram_banner.set(display.memory_warning(self.memory,reserve)+secondary)

    def draw_chart(self):
        self.chart.delete('all'); width=max(200,self.chart.winfo_width()); height=145
        maximum=max([a for point in self.hist for a in point]+[1])*1.1
        self.chart.create_text(8,8,anchor='nw',fill='white',text=f'{maximum:.1f} GiB')
        for index,color in [(0,'#72aaff'),(1,'#50e3b1')]:
            points=[]
            for i,point in enumerate(self.hist): points.extend((45+i*(width-55)/max(1,len(self.hist)-1),height-10-point[index]/maximum*(height-30)))
            if len(points)>=4: self.chart.create_line(*points,fill=color,width=2)

    def render_requests(self):
        rows=[]
        for gateway in self.gateways.values():
            with gateway.lock: rows.extend(list(gateway.records))
        rows=rows[-100:]
        if rows==self.metrics: return
        self.metrics=rows
        self.request_tree.delete(*self.request_tree.get_children())
        for row in reversed(rows):
            fmt=lambda x: 'Unavailable' if x is None else f'{x:.2f}' if isinstance(x,float) else str(x)
            used=row.get('context_used');limit=row.get('context_limit')
            window=f'{used:,} / {limit:,} ({used/limit:.0%})' if used is not None and limit else 'Not reported'
            self.request_tree.insert('','end',values=(row['time'],row['model'],fmt(row['prompt_tokens']),fmt(row['output_tokens']),window,
                fmt(row['first_token_s']),fmt(row['effective_tps']),fmt(row['generation_tps']),str(row['status'])+(' complete' if row['complete'] else ' interrupted')))

    def process_details(self,event=None):
        text='\n\n'.join(f"{self.processes[k]['name']}\n{self.processes[k].get('path','')}\n{self.processes[k].get('command','')}" for k in self.process_tree.selection() if k in self.processes)
        self.proc_detail.configure(state='normal'); self.proc_detail.delete('1.0','end'); self.proc_detail.insert('1.0',text); self.proc_detail.configure(state='disabled')

    def process_menu(self,event):
        row=self.process_tree.identify_row(event.y)
        if not row: return
        if row not in self.process_tree.selection(): self.process_tree.selection_set(row)
        try: self.menu.tk_popup(event.x_root,event.y_root)
        finally: self.menu.grab_release()

    def stop_selected(self):
        rows=[self.processes[k].copy() for k in self.process_tree.selection() if k in self.processes]
        if not rows: return
        names='\n'.join(r['name']+' '+str(r['pid']) for r in rows)
        if not messagebox.askyesno('Stop these items?',names+'\n\nThis interrupts active answers/tools. Ollama models are unloaded; processes are ended. Continue?'): return
        def work():
            errors=[]
            for row in sorted(rows,key=lambda r:r['kind']!='Ollama loaded model'):
                try:
                    if row['kind']=='Ollama loaded model': core.api(row['base'],'/api/generate',{'model':row['name'],'keep_alive':0,'stream':False},timeout=30)
                    else: core.stop_process(row)
                except Exception as exc: errors.append(row['name']+': '+str(exc))
            self.events.put(('stopped',errors))
        threading.Thread(target=work,daemon=True).start()

    def tick(self):
        self.refresh_live()
        if not self.closing: self.root.after(3000,self.tick)

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='inventory':
                    self.busy_inventory=False; rows,warnings=data; self.models={r['id']:r for r in rows}; self.render_models()
                    plain=[]
                    if any(w.startswith('Ollama inventory unavailable') for w in warnings):plain.append('Ollama is off or cannot be reached. Its saved models are still listed.')
                    if any(not w.startswith('Ollama inventory unavailable') for w in warnings):plain.append('Some model details could not be read.')
                    self.status.set(f'{len(rows)} model entries found. '+(' '.join(plain) if plain else 'Your model list is up to date.'))
                    self.refresh_chat_models()
                elif kind=='inventory_error': self.busy_inventory=False; self.status.set(data)
                elif kind=='live': self.busy_live=False; self.render_live(data)
                elif kind=='live_error': self.busy_live=False; self.status.set(data)
                elif kind=='gpu':
                    self.busy_gpu=False; self.gpu=data;self.installed_memory=data.get('installed_physical');self.update_memory_header()
                    self.gpu_label.set('GPU counters: '+'; '.join(f'{name}: dedicated {self.gib(v["dedicated"])}, shared {self.gib(v["shared"])}' for name,v in data.get('adapters',{}).items()) if data.get('available') else data.get('reason','GPU counters unavailable'))
                elif kind=='stopped':
                    if data: messagebox.showerror('Could not stop some items','\n'.join(data))
                    self.refresh_live()
                elif kind=='chat_models':
                    self.chat_discovering=False;self.chat_targets={t['label']:t for t in data}
                    self.chat_models.configure(values=tuple(self.chat_targets))
                    if self.chat_model.get() not in self.chat_targets:
                        self.chat_model.set(next(iter(self.chat_targets),'No running chat model detected'));self.chat_history=[]
                    if not self.chat_busy:self.chat_status.set('Using an already-running local model. No extra model was loaded.' if data else 'No running chat model found. The built-in RAM check still works; start your usual model to enable AI chat.')
                elif kind=='chat_models_error':
                    self.chat_discovering=False;self.chat_status.set('Could not inspect running models: '+data)
                elif kind=='chat_reply':
                    self.chat_busy=False;self.chat_send.configure(state='normal');self.chat_models.configure(state='readonly')
                    if self.chat_cancel.is_set():
                        self.copy_after_check=False
                        self.append_chat('Hub','Reply cancelled. No proposed actions were added or applied.');self.chat_status.set('Cancelled. No changes made.');continue
                    if data.get('snapshot'):self.last_snapshot=data['snapshot']
                    self.append_chat('Memory answer' if data['diagnostic'] else 'Local assistant',data['answer'])
                    if data['diagnostic'] and self.fix_before is not None:
                        now=data['snapshot']['memory_bytes']['available'];change=now-self.fix_before;self.fix_before=None
                        self.append_chat('After the fix',f'Free RAM is now {helpdesk.gb(now)}. '+(f'That is {helpdesk.gb(change)} more than before.' if change>=0 else f'That is {helpdesk.gb(-change)} less than before.')+' Other apps can change memory use too. A graphics-memory change may not show as free system RAM.')
                    if data['prompt']:
                        self.chat_history.extend([{'role':'user','content':data['prompt']},{'role':'assistant','content':data['answer']}]);self.chat_history=self.chat_history[-12:]
                    if data['diagnostic']:
                        for old in self.chat_proposals.values():
                            if old.get('origin')=='scan' and old['state']=='Pending':old['state']='Old scan';self.proposal_tree.set(old['id'],'state','Old scan')
                    for p in data['proposals']:
                        if data['diagnostic']:p['origin']='scan'
                        self.add_proposal(p)
                    self.chat_status.set(f'{len(data["proposals"])} fix choice(s) below. Nothing changed yet.' if data['proposals'] else 'Check complete. No automatic fix is supported by the current readings. Copy the help report for more help.')
                    if getattr(self,'copy_after_check',False) and self.last_snapshot:
                        self.copy_after_check=False
                        report=helpdesk.help_report(self.last_snapshot,self.models,self.profiles,self.metrics)
                        self.root.clipboard_clear();self.root.clipboard_append(report)
                        self.append_chat('Hub','Help report copied. Paste it into the other AI yourself. It includes memory readings, app names and model settings. It leaves out prompts, passwords, keys and file paths. Nothing was uploaded.')
                elif kind=='chat_error':
                    self.copy_after_check=False
                    self.chat_busy=False;self.chat_send.configure(state='normal');self.chat_models.configure(state='readonly')
                    self.append_chat('Hub','Chat could not finish: '+data+'\nNo changes were applied. The built-in RAM check is still available.')
                    self.chat_status.set('Chat unavailable or cancelled. No changes made.')
                elif kind=='chat_action':
                    key,error=data;p=self.chat_proposals[key];p['state']='Failed' if error else 'Applied'
                    if error:self.fix_before=None
                    self.proposal_tree.set(key,'state',p['state'])
                    self.append_chat('Hub',('Action failed: '+error) if error else 'Completed: '+p['summary'])
                    self.refresh_live();self.refresh_chat_models()
                    self.root.after(2000,self.check_after_fix)
                elif kind=='repair_progress':
                    self.status.set(data)
                    if self.repair_view and self.repair_view['window'].winfo_exists():
                        self.repair_view['status'].set(data);self.repair_view['detail'].insert('end',data+'\n');self.repair_view['detail'].see('end')
                elif kind=='repair_result':
                    self.repair_busy=False;row,result,error=data;saved=False
                    core.save_json(core.STATE/'last_gpu_test.json',{'model':row['name'],'result':result,'error':error})
                    if self.repair_view and self.repair_view['window'].winfo_exists():
                        view=self.repair_view;view['cancel'].configure(text='Close',command=view['window'].destroy);view['window'].protocol('WM_DELETE_WINDOW',view['window'].destroy)
                        if result and result['status']=='passed':
                            view['status'].set('Passed: the model finished both answers and GPU use was confirmed. Nothing saved yet.')
                            view['save'].configure(state='normal',command=lambda row=row,result=result:self.save_gpu_setup(row,result))
                            view['detail'].insert('end','\nPassed setup: '+result['choice']['backend']+'; '+str(result['choice']['profile']['context'])+' tokens.\n'+json.dumps(result['evidence'],indent=2))
                            if result.get('optimized'):
                                view['detail'].insert('end','\n\n'+result['recommendation']+'\n')
                                for item in result['comparison']:
                                    speed=item.get('benchmark',{}).get('job_tps')
                                    writing=item.get('benchmark',{}).get('generation_tps')
                                    view['detail'].insert('end',item['backend']+f' / {item["context"]:,} context tokens: writing '+(f'{writing:.2f} tokens/sec' if writing else 'not reported')+'; whole job '+(f'{speed:.2f} tokens/sec' if speed else 'not reported')+'\n')
                                    if item.get('context_probe'):view['detail'].insert('end','Large input checked: '+str(item['context_probe']['input_tokens'])+' tokens.\n')
                                performance=result.get('performance_goal',{})
                                if performance.get('minimum_met') and performance.get('context_goal_met',True):
                                    saved=self.save_gpu_setup(row,result,confirm=False)
                                    view['status'].set(('Recommended setup saved. Context and speed minimums were met. '+('The speed target was also met.' if performance.get('target_met') else 'The higher speed target was not reached. You can try another model.')) if saved else 'The test passed, but the setup was not saved. Read the error and try saving again.')
                                else:
                                    speed=performance.get('measured_writing_tps');context=result['choice']['profile']['context']
                                    view['status'].set('No setup met both minimums. Selected fallback: '+(f'{speed:.2f} writing tokens/sec' if speed else 'writing speed not reported')+f', {context:,} context tokens. Nothing saved automatically.')
                                    view['save'].configure(text='Save anyway…')
                        else:
                            view['status'].set('No passing setup was saved. '+(error or 'Read the test results below.'))
                            view['detail'].insert('end','\n'+(error or json.dumps(result['attempts'],indent=2)))
                    self.status.set('Recommended model default saved. You can tinker with it or Undo.' if saved else 'Test finished. Check the result window. No new default was saved automatically.')
                elif kind=='verified_started':
                    self.repair_busy=False;self.finish_verified_start(data)
                elif kind=='verified_start_error':
                    self.repair_busy=False;model_id,error=data
                    recipes=core.read_json(core.STATE/'verified_recipes.json')
                    if model_id in recipes:recipes[model_id]['status']='needs_retest';recipes[model_id]['last_failure']=error;core.save_json(core.STATE/'verified_recipes.json',recipes)
                    self.status.set('The saved setup did not pass today. OpenCode was not started. Test again.');self.choose_model()
                    messagebox.showerror('GPU check failed',error+'\n\nOpenCode was not started with an unverified model. The saved setup now needs a new test.')
                elif kind=='managed_alert':
                    item,reason=data;item['warned']=True
                    if reason=='The tested model server stopped.':
                        self.status.set(item['row']['name']+' stopped. Its saved setup is kept; Start in OpenCode checks it again.')
                        self.choose_model();continue
                    recipes=core.read_json(core.STATE/'verified_recipes.json');model_id=item['row']['id']
                    if model_id in recipes:recipes[model_id]['status']='needs_retest';recipes[model_id]['last_failure']=reason;core.save_json(core.STATE/'verified_recipes.json',recipes)
                    self.status.set('GPU warning for '+item['row']['name']+': '+reason+' Test this model again. No other model was stopped.')
                    self.recipe_label.set('GPU setup needs a new test: '+reason)
        except queue.Empty: pass
        if not self.closing: self.root.after(150,self.poll)

    def close(self):
        if self.repair_busy:
            self.repair_cancel.set();messagebox.showinfo('Stopping the GPU test','The test is stopping its own model server. Wait for it to finish, then close the app again.');return
        if self.chat_busy:self.chat_cancel.set()
        if self.gateways and not messagebox.askyesno('Close the hub?', 'Hub-launched terminals use this app as their model connection. Closing it disconnects them. Private servers for tested setups will stop. Other model servers stay running. Close anyway?'): return
        self.closing=True
        for gateway in self.gateways.values(): threading.Thread(target=gateway.close,daemon=True).start()
        for item in self.managed_sessions:threading.Thread(target=item['session'].stop,daemon=False).start()
        self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); Hub(root); root.mainloop()
