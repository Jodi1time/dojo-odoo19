"""Fail if required source exists only in a runner, not in the published commit."""
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[2]
required=['companion/package.json','companion/package-lock.json','companion/src/contracts/kiosk-attendance.ts']
required += [str(p.relative_to(root)) for p in (root/'addons').glob('*/security/ir.access.csv')]
for name in required:
 subprocess.run(['git','ls-files','--error-unmatch',name],cwd=root,check=True,stdout=subprocess.DEVNULL)
 data=subprocess.check_output(['git','show','HEAD:'+name],cwd=root)
 if data!=(root/name).read_bytes():raise RuntimeError('Published content mismatch: '+name)
subprocess.run(['git','diff','--exit-code','HEAD','--','addons','companion','tools/monday'],cwd=root,check=True)
print('Required manifests, lockfile, security access rules and application source match the published commit.')
