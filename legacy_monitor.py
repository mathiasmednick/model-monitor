"""Local Windows model monitor. Python standard library only."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlparse

K = C.WinDLL('kernel32', use_last_error=True)
N = C.WinDLL('ntdll')
P = C.WinDLL('psapi')
HANDLE = W.HANDLE
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = HANDLE
K.CloseHandle.argtypes = [HANDLE]
K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
K.CreateToolhelp32Snapshot.restype = HANDLE

class Entry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('exe', W.WCHAR * 260)]

class Mem(C.Structure):
    _fields_ = [('cb', W.DWORD), ('faults', W.DWORD)] + [(x, C.c_size_t) for x in
        ['peak', 'working', 'qpeak', 'quota', 'pqpeak', 'pquota', 'page', 'pagepeak', 'private']]

class Unicode(C.Structure):
    _fields_ = [('length', W.USHORT), ('maximum', W.USHORT), ('buffer', C.c_void_p)]

K.Process32FirstW.argtypes = [HANDLE, C.POINTER(Entry)]
K.Process32NextW.argtypes = [HANDLE, C.POINTER(Entry)]
K.QueryFullProcessImageNameW.argtypes = [HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
K.GetProcessTimes.argtypes = [HANDLE] + [C.POINTER(W.FILETIME)] * 4
K.TerminateProcess.argtypes = [HANDLE, W.UINT]
P.GetProcessMemoryInfo.argtypes = [HANDLE, C.POINTER(Mem), W.DWORD]
N.NtQueryInformationProcess.argtypes = [HANDLE, W.ULONG, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)]
N.NtQueryInformationProcess.restype = W.LONG

def birth(handle):
    times = [W.FILETIME() for _ in range(4)]
    if not K.GetProcessTimes(handle, *[C.byref(t) for t in times]):
        return None
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime

def command_line(handle):
    needed = W.ULONG()
    N.NtQueryInformationProcess(handle, 60, None, 0, C.byref(needed))
    if not 0 < needed.value < 2**20:
        return ''
    buf = C.create_string_buffer(needed.value)
    if N.NtQueryInformationProcess(handle, 60, buf, len(buf), C.byref(needed)) < 0:
        return ''
    u = Unicode.from_buffer(buf)
    # Never dereference a pointer outside the returned allocation.
    if u.buffer and C.addressof(buf) <= u.buffer and u.buffer + u.length <= C.addressof(buf) + len(buf):
        return C.wstring_at(u.buffer, u.length // 2)
    return ''

def processes():
    snap = K.CreateToolhelp32Snapshot(2, 0)
    if snap == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    rows = []
    try:
        e = Entry(); e.size = C.sizeof(e)
        ok = K.Process32FirstW(snap, C.byref(e))
        while ok:
            name = e.exe.lower()
            # Include known engines and controllers. Never scan or kill arbitrary Python/Node apps.
            if any(s in name for s in ('ollama', 'llama-server', 'llama-cli', 'strata', 'lm studio', 'lm-studio', 'llmster')):
                h = K.OpenProcess(0x1000 | 0x10, False, e.pid)
                if not h:
                    h = K.OpenProcess(0x1000, False, e.pid)
                path = ''; cmd = ''; created = None; ram = None
                if h:
                    try:
                        b = C.create_unicode_buffer(32768); n = W.DWORD(len(b))
                        if K.QueryFullProcessImageNameW(h, 0, b, C.byref(n)): path = b.value
                        cmd = command_line(h); created = birth(h)
                        m = Mem(); m.cb = C.sizeof(m)
                        if P.GetProcessMemoryInfo(h, C.byref(m), m.cb): ram = m.working
                    finally: K.CloseHandle(h)
                match = re.search(r'(?:^|\s)(?:-m|--model|--model-path)(?:\s+|=)(?:"([^"]+)"|(\S+))', cmd)
                model = Path(match.group(1) or match.group(2)).name if match else 'Model name unavailable'
                runtime = 'Ollama' if 'ollama' in name else 'Strata' if 'strata' in name or 'strata' in path.lower() else 'llama.cpp / other'
                controller = any(s in name for s in ('strata', 'lm studio', 'lm-studio', 'llmster')) or ('ollama' in name and 'runner' not in cmd.lower())
                rows.append(dict(key=f'p:{e.pid}:{created}', kind='process', pid=e.pid, birth=created,
                    runtime=runtime, model=model if not controller else 'App / service (may host models)',
                    state='Controller / service' if controller else 'Engine process', ram=ram, vram=None,
                    detail=f'{path or e.exe}\n\n{cmd or "Command line unavailable (permissions or process exited)."}', name=e.exe))
            ok = K.Process32NextW(snap, C.byref(e))
    finally: K.CloseHandle(snap)
    return rows

HTTP = build_opener(ProxyHandler({}))

def endpoint(value):
    value = value.strip()
    if '://' not in value: value = 'http://' + value
    u = urlparse(value)
    if u.scheme != 'http' or u.hostname not in ('localhost', '127.0.0.1', '::1') or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('Use a local HTTP address, for example http://127.0.0.1:11434')
    return value.rstrip('/')

def api(base, route, body=None):
    request = Request(base + route, data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    with HTTP.open(request, timeout=5 if body is None else 20) as response:
        return json.load(response)

def scan(base):
    rows = processes(); error = ''
    try:
        data = api(base, '/api/ps')
        for m in data.get('models', []):
            name = m.get('name') or m['model']
            rows.insert(0, dict(key='o:' + name, kind='model', runtime='Ollama', model=name,
                pid='', state='Loaded in memory', ram=None, vram=m.get('size_vram'), base=base,
                detail=f"Model: {name}\nUnloads at: {m.get('expires_at', 'unknown')}\nModel size: {gib(m.get('size'))}\nEndpoint: {base}"))
    except Exception as exc:
        error = f'Ollama API unavailable: {exc}'
    return rows, error

def gib(n):
    return 'Unavailable' if n is None else f'{n / 1024**3:.2f} GiB'

def stop_row(row):
    if row['kind'] == 'model':
        api(row['base'], '/api/generate', {'model': row['model'], 'keep_alive': 0, 'stream': False})
        return
    if row['birth'] is None:
        raise RuntimeError('Cannot safely identify this process. Try running the monitor as administrator.')
    h = K.OpenProcess(0x1000 | 1, False, row['pid'])
    if not h: raise C.WinError(C.get_last_error())
    try:
        if birth(h) != row['birth']: raise RuntimeError('Process exited or changed; refresh first.')
        if not K.TerminateProcess(h, 0): raise C.WinError(C.get_last_error())
    finally: K.CloseHandle(h)

class App:
    def __init__(self, root):
        self.root = root; self.rows = {}; self.busy = False; self.events = queue.Queue()
        root.title('Model Monitor'); root.geometry('1160x720'); root.minsize(850, 560)
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('Treeview', rowheight=32, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        style.configure('TLabel', font=('Segoe UI', 10))
        style.configure('TButton', padding=8)
        frame = ttk.Frame(root, padding=18); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Model Monitor', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='See what is still loaded. Unload models or stop local engines.').pack(anchor='w', pady=(3, 12))
        bar = ttk.Frame(frame); bar.pack(fill='x')
        ttk.Label(bar, text='Ollama address:').pack(side='left')
        initial = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
        self.address = tk.StringVar(value=initial)
        ttk.Entry(bar, textvariable=self.address, width=33).pack(side='left', padx=8)
        self.refresh_button = ttk.Button(bar, text='Refresh', command=self.refresh); self.refresh_button.pack(side='left')
        self.stop_button = ttk.Button(bar, text='Stop selected', command=self.stop); self.stop_button.pack(side='right')
        self.summary = tk.StringVar(value='Checking local models…')
        ttk.Label(frame, textvariable=self.summary, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=12)
        area = ttk.Frame(frame); area.pack(fill='both', expand=True)
        cols = ('runtime', 'model', 'pid', 'state', 'ram', 'vram')
        self.tree = ttk.Treeview(area, columns=cols, show='headings', selectmode='extended')
        for col, title, width in zip(cols, ['Runtime', 'Model / app', 'PID', 'Status', 'Process RAM', 'Ollama VRAM'], [120, 320, 65, 160, 110, 110]):
            self.tree.heading(col, text=title); self.tree.column(col, width=width, minwidth=55)
        scroll = ttk.Scrollbar(area, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set); scroll.pack(side='right', fill='y'); self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.details)
        self.tree.bind('<Button-3>', self.context)
        self.tree.bind('<Control-a>', self.select_all)
        self.menu = tk.Menu(root, tearoff=False); self.menu.add_command(label='Stop selected', command=self.stop)
        ttk.Label(frame, text='Click to select • Shift+click for a range • Ctrl+click to add/remove • Right-click to stop').pack(anchor='w', pady=(10, 3))
        self.detail = tk.Text(frame, height=4, wrap='word', font=('Segoe UI', 9), relief='flat', background='#edf1f5')
        self.detail.pack(fill='x'); self.detail.configure(state='disabled')
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=1050).pack(anchor='w', pady=(8, 0))
        ttk.Label(frame, text='VRAM is reported only by Ollama; engine process RAM is separate. Controllers do not prove a model is loaded.\nApps may reload a stopped model when another request arrives. Closing this monitor leaves your models running.', wraplength=1050).pack(anchor='w', pady=(7, 0))
        root.after(100, self.refresh); root.after(100, self.poll); root.after(4000, self.tick)

    def select_all(self, event):
        self.tree.selection_set(self.tree.get_children()); return 'break'

    def details(self, event=None):
        selected = self.tree.selection()
        text = '\n\n'.join(self.rows[k]['detail'] for k in selected if k in self.rows)
        self.detail.configure(state='normal'); self.detail.delete('1.0', 'end'); self.detail.insert('1.0', text); self.detail.configure(state='disabled')

    def context(self, event):
        row = self.tree.identify_row(event.y)
        if not row: return
        if row not in self.tree.selection(): self.tree.selection_set(row)
        try: self.menu.tk_popup(event.x_root, event.y_root)
        finally: self.menu.grab_release()

    def tick(self):
        # Don't repaint under an active range selection or context menu.
        if not self.tree.selection(): self.refresh()
        self.root.after(4000, self.tick)

    def refresh(self):
        if self.busy: return
        try: base = endpoint(self.address.get())
        except ValueError as exc: self.status.set(str(exc)); return
        self.busy = True; self.status.set('Refreshing…')
        def work():
            try: self.events.put(('scan', scan(base)))
            except Exception as exc: self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.busy: return
        chosen = [self.rows[k] for k in self.tree.selection() if k in self.rows]
        if not chosen: return
        names = '\n'.join((r['model'] if r['kind'] == 'model' else f"{r['name']} (PID {r['pid']})") for r in chosen)
        if not messagebox.askyesno('Stop selected items?', f'{names}\n\nOllama models will be unloaded. Selected processes will be ended immediately, interrupting any active work. Continue?'): return
        self.busy = True; self.status.set('Stopping selected items…')
        def work():
            errors = []
            # Unload models before ending any selected service processes.
            for row in sorted(chosen, key=lambda r: r['kind'] != 'model'):
                try: stop_row(row)
                except Exception as exc: errors.append(f"{row['model']} / {row.get('pid', '')}: {exc}")
            self.events.put(('stopped', errors))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait(); self.busy = False
                if kind == 'scan':
                    rows, error = payload; selected = self.tree.selection()
                    self.rows = {r['key']: r for r in rows}
                    for key in self.tree.get_children():
                        if key not in self.rows: self.tree.delete(key)
                    for r in rows:
                        values = (r['runtime'], r['model'], r['pid'], r['state'], gib(r['ram']), gib(r['vram']))
                        if self.tree.exists(r['key']): self.tree.item(r['key'], values=values)
                        else: self.tree.insert('', 'end', iid=r['key'], values=values)
                    self.tree.selection_set([k for k in selected if k in self.rows])
                    models = [r for r in rows if r['kind'] == 'model']
                    total = sum(r['vram'] or 0 for r in models)
                    self.summary.set(f'{len(models)} loaded Ollama model(s)  •  {len(rows)-len(models)} runtime process(es)  •  Ollama VRAM: {gib(total) if not error else "Unavailable"}')
                    self.status.set(error or ('Updated. No recognized model runtimes found.' if not rows else 'Updated. Refresh pauses while rows are selected; click Refresh to check again.'))
                    self.details()
                elif kind == 'stopped':
                    if payload: messagebox.showerror('Some items could not be stopped', '\n'.join(payload))
                    self.refresh()
                else: self.status.set(payload)
        except queue.Empty: pass
        self.root.after(100, self.poll)

if __name__ == '__main__':
    root = tk.Tk()
    icon = Path(__file__).with_name('model_monitor.ico')
    if icon.exists(): root.iconbitmap(str(icon))
    App(root); root.mainloop()
