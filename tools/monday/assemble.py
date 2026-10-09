"""Materialize one integrated review tree. Never touches Justin's remote branches."""
from pathlib import Path
import json, shutil, subprocess, tempfile
ROOT=Path(__file__).resolve().parents[2]
TEAM='e54466e91e3cf848fcc4046dc1711ded988de0d5'
OLD='0566576641b23e24ddf8da5dd9abe4170d107b81'
UI='c108399f38093c01f2e1f187e7feaa929a43eace'
def run(*args,**kw): return subprocess.run(args,check=True,**kw)
def replace(path,before,after):
 p=ROOT/'companion'/path;s=p.read_text()
 if after in s:return
 if s.count(before)!=1:raise RuntimeError('Edit marker changed: '+path+' '+before[:80])
 p.write_text(s.replace(before,after,1))
run('git','fetch','--no-tags','https://github.com/jDelille/dojo-odoo19.git',TEAM,cwd=ROOT)
diff=subprocess.check_output(['git','diff','--binary',OLD,TEAM,'--','addons'],cwd=ROOT)
check=subprocess.run(['git','apply','--check','-'],input=diff,cwd=ROOT,capture_output=True)
if check.returncode==0:run('git','apply','-',input=diff,cwd=ROOT)
else:run('git','apply','--reverse','--check','-',input=diff,cwd=ROOT)
front=ROOT/'companion'
if not (front/'package.json').exists():
 with tempfile.TemporaryDirectory() as tmp:
  src=Path(tmp)/'upstream'
  run('git','clone','--no-checkout','https://github.com/jDelille/companion.git',str(src))
  run('git','checkout','--detach',UI,cwd=src)
  for a,b in [('app/api/v2/sessions/route.ts','src/integrations/mock/routes/sessions.ts'),('app/api/v2/sessions/[sessionId]/roster/route.ts','src/integrations/mock/routes/roster.ts'),('app/api/v2/attendance/check-ins/route.ts','src/integrations/mock/routes/checkins.ts')]:
   target=src/b;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src/a,target)
  shutil.rmtree(src/'.git')
  shutil.copytree(front,src,dirs_exist_ok=True)
  shutil.rmtree(front);shutil.copytree(src,front)
  (front/'UPSTREAM.md').write_text('UI source: jDelille/companion at '+UI+'\nIntegration overlay: Jodi1time/dojo-odoo19 release/monday-demo. Justin remains the author of the original screens.\n')
replace('src/components/people/member-360/Member360View.tsx','  attendance: MemberAttendance;','  attendance: MemberAttendance;\n  liveTest?: boolean;')
replace('src/components/people/member-360/Member360View.tsx','({ member, attendance }: Props)','({ member, attendance, liveTest = false }: Props)')
replace('src/components/people/member-360/Member360View.tsx','<MemberGrid attendance={attendance} />','<MemberGrid attendance={attendance} liveMember={liveTest ? member : undefined} />')
replace('src/components/people/member-360/Member360View.tsx','<Identity member={member}/>','<Identity member={member} readOnly={liveTest}/>')
grid='src/components/people/member-360/member-grid/MemberGrid.tsx'
replace(grid,'import type { MemberAttendance }','import type { Member, MemberAttendance }')
replace(grid,'  attendance: MemberAttendance;','  attendance: MemberAttendance;\n  liveMember?: Member;')
replace(grid,'({ attendance }: Props)','({ attendance, liveMember }: Props)')
replace(grid,'const story = [attendanceStory(attendance), ...storyPlaceholders];','const story = [attendanceStory(attendance), ...(liveMember ? [{ label: "Current rank", value: liveMember.rank.name }] : storyPlaceholders)];')
replace(grid,'const recent = [lastCheckIn(attendance), ...recentPlaceholders];','const recent = [lastCheckIn(attendance), ...(liveMember ? [] : recentPlaceholders)];')
replace(grid,'<h2>Active and progressing normally.</h2>','<h2>{liveMember ? "Verified attendance" : "Active and progressing normally."}</h2>')
replace(grid,'<StatList stats={training} onEdit={() => setOpen("edit")} />','<StatList stats={liveMember ? [{label:"Training details",value:"Not connected in this test"}] : training} onEdit={liveMember ? undefined : () => setOpen("edit")} />')
replace(grid,'<StatList stats={progression} />','<StatList stats={liveMember ? [{label:"Current rank",value:liveMember.rank.name}] : progression} />')
identity='src/components/people/member-360/identity/Identity.tsx'
replace(identity,'  member: Member;','  member: Member;\n  readOnly?: boolean;')
replace(identity,'({ member }: Props)','({ member, readOnly = false }: Props)')
replace(identity,'<div className={styles.member__avatar}>MC</div>','<div className={styles.member__avatar}>{member.name.split(" ").filter(Boolean).map(p=>p[0]).slice(0,2).join("")}</div>')
replace(identity,'disabled={btn.label === "Check in" && checkedIn}','disabled={readOnly || (btn.label === "Check in" && checkedIn)}')
replace(identity,'<p>Children Advanced · {member.householdName}</p>','<p>{readOnly ? member.memberNumber : <>Children Advanced · {member.householdName}</>}</p>')
kiosk='app/(kiosk)/kiosk/page.tsx'
replace(kiosk,'import { startTransition, useEffect, useReducer } from "react";','import { startTransition, useEffect, useReducer } from "react";\nimport OfflineAttendanceStatus from "@/components/kiosk/OfflineAttendanceStatus";')
replace(kiosk,'      {renderScreen()}','      <OfflineAttendanceStatus />\n      {renderScreen()}')
p=front/'src/integrations/kiosk.ts';s=p.read_text()
if 'offline-attendance' not in s:
 s='import {queueAttendance} from "./offline-attendance";\n'+s
 s=s.replace('return { kind: "noAnswer" };','await queueAttendance(command); return { kind: "noAnswer" };')
 p.write_text(s)
p=front/'package.json';pkg=json.loads(p.read_text())
pkg.setdefault('scripts',{}).update({'test:integration':'bash tools/test-integration.sh','test:e2e':'playwright test','typecheck':'tsc --noEmit'})
pkg.setdefault('devDependencies',{}).update({'@playwright/test':'^1.56.0','fake-indexeddb':'^6.0.0'})
p.write_text(json.dumps(pkg,indent=2)+'\n')
print('Materialized one integrated application. No customer databases or teammate branches modified.')
