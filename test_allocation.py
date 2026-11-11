"""Exercise the elevated repair body with fake registry and disk APIs only."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import hub_core as core

class AllocationTests(unittest.TestCase):
    def test_apply_undo_conflict_and_disk_reserve_without_system_changes(self):
        work=core.STATE/'test-work';work.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as temp:
            source=(core.HERE/'repair_allocation.ps1').read_text(encoding='utf-8')
            body=source[source.index('    $key ='):source.index('} catch {')]
            location="'"+temp.replace("'","''")+"'"
            body=body.replace('$repairRoot',location)
            harness=r'''
$ErrorActionPreference='Stop'
$script:values=@('c:\pagefile.sys 65536 98304','D:\pagefile.sys 1024 2048')
$script:space=200GB
function Get-ItemProperty { param($LiteralPath,$Name) [pscustomobject]@{PagingFiles=$script:values} }
function Set-ItemProperty { param($LiteralPath,$Name,$Type,$Value) $script:values=@($Value) }
function Get-PSDrive { param($Name) [pscustomobject]@{Free=$script:space} }
function Apply-Repair { param([switch]$Undo)
''' + body + '\n}\n' + r'''
Apply-Repair
if ($script:values.Count -ne 2 -or $script:values -notcontains 'C:\pagefile.sys 131072 196608' -or $script:values -notcontains 'D:\pagefile.sys 1024 2048') {throw 'Apply did not preserve other drives.'}
Apply-Repair -Undo
if ($script:values -notcontains 'c:\pagefile.sys 65536 98304') {throw 'Undo did not restore original.'}
$script:space=100GB
$refused=$false
try {Apply-Repair} catch {$refused=$_.Exception.Message -like '*144 GB*'}
if (-not $refused) {throw 'Low disk space was not rejected.'}
$script:space=200GB
$script:values=@('c:\pagefile.sys 1234 5678')
$refused=$false
try {Apply-Repair -Undo} catch {$refused=$_.Exception.Message -like '*changed outside*'}
if (-not $refused -or $script:values[0] -ne 'c:\pagefile.sys 1234 5678') {throw 'Undo overwrote an external change.'}
'Mock repair checks passed'
'''
            path=Path(temp)/'mock-repair.ps1';path.write_text(harness,encoding='utf-8-sig')
            command='& ([scriptblock]::Create([IO.File]::ReadAllText(' + "'"+str(path).replace("'","''")+"'" + ')))'
            result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('Mock repair checks passed',result.stdout)

if __name__=='__main__':unittest.main()
