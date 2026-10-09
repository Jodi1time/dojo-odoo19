#!/usr/bin/env python3
"""Import the pinned existing Companion into this isolated release branch.
No customer data, remote writes, installs, or builds are performed by this script.
"""
from pathlib import Path
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = 'c108399f38093c01f2e1f187e7feaa929a43eace'
DEST = ROOT / 'companion'

def main():
    if DEST.exists():
        raise SystemExit('Companion already exists. Review updates against its committed source instead of overwriting it.')
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / 'source'
        subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(repo),'fetch','--depth=1','https://github.com/jDelille/companion.git',PIN],check=True)
        subprocess.run(['git','-C',str(repo),'checkout','--detach','FETCH_HEAD'],check=True)
        if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=PIN:
            raise SystemExit('Wrong source commit')
        archive=subprocess.check_output(['git','-C',str(repo),'archive','HEAD'])
        target=Path(tmp)/'prepared';target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target,filter='data')
        contract=(target/'src/contracts/kiosk-attendance.ts').read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(contract)).encode()+b'\0'+contract).hexdigest()
        if blob!='cdd1af9937617a60cbf2da8afad2fb53d2cdab20':
            raise SystemExit('Shared contract differs from reviewed source')
        for source,dest in {
            'app/api/v2/sessions/route.ts':'src/integrations/mock/routes/sessions.ts',
            'app/api/v2/sessions/[sessionId]/roster/route.ts':'src/integrations/mock/routes/roster.ts',
            'app/api/v2/attendance/check-ins/route.ts':'src/integrations/mock/routes/checkins.ts',
        }.items():
            path=target/dest;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(target/source,path)
        edits=json.loads((ROOT/'delivery/profile-edits.json').read_text())
        for path,replacements in edits.items():
            file=target/path;content=file.read_text()
            for before,after in replacements:
                if content.count(before)!=1:raise SystemExit('Edit marker mismatch: '+path)
                content=content.replace(before,after,1)
            file.write_text(content)
        for file in (ROOT/'delivery/frontend').rglob('*'):
            if file.is_file():
                path=target/file.relative_to(ROOT/'delivery/frontend')
                path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,path)
        package=json.loads((target/'package.json').read_text())
        package['scripts']['test:odoo-integration']='bash tools/test-odoo-gateway.sh'
        (target/'package.json').write_text(json.dumps(package,indent=2)+'\n')
        (target/'SOURCE-BASELINE.md').write_text('# Source attribution\n\nExisting UI by Justin, imported from jDelille/companion at '+PIN+'.\nThe shared kiosk contract is unchanged. Test-system integration changes are maintained in this release branch. No production readiness is implied.\n')
        shutil.copytree(target,DEST)
    print('Prepared Companion source at companion/. No remote branch changed by this script.')

if __name__=='__main__':main()
