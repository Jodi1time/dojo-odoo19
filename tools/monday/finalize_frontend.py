"""Connect existing shell slots instead of exposing mock operational numbers."""
from pathlib import Path
root=Path(__file__).resolve().parents[2]/'companion'
def change(file,before,after):
 p=root/file;s=p.read_text()
 if after in s:return
 if s.count(before)!=1:raise RuntimeError('Marker changed: '+file)
 p.write_text(s.replace(before,after,1))
source=root/'app/(kiosk)/integration/companion/page.tsx'
target=root/'src/components/ai/ConnectedCompanion.tsx'
if not target.exists():
 s=source.read_text().replace('import {useEffect,useState,useRef} from "react";','import {useEffect,useState,useRef} from "react";\nimport {usePathname} from "next/navigation";')
 s=s.replace('export default function Companion(){','export default function Companion(){const pathname=usePathname();')
 s=s.replace('setMemberId(new URLSearchParams(location.search).get("memberId")||"");},[]);','setMemberId(pathname.match(/^\\/people\\/(\\d+)$/)?.[1]||new URLSearchParams(location.search).get("memberId")||"");setCount(null);setSummary("");setReceipt("");setReview(false);command.current=null;},[pathname]);')
 target.parent.mkdir(parents=True,exist_ok=True);target.write_text(s)
 source.write_text('export {default} from "@/components/ai/ConnectedCompanion";\n')
change('app/(companion)/layout.tsx','import AdaptiveShell from "@/components/shell/AdaptiveShell";','import AdaptiveShell from "@/components/shell/AdaptiveShell";\nimport ConnectedCompanion from "@/components/ai/ConnectedCompanion";\nimport {odooTestMode} from "@/server/odoo-runtime";')
change('app/(companion)/layout.tsx','<AdaptiveShell>{children}</AdaptiveShell>','<AdaptiveShell connectedTest={odooTestMode()} context={odooTestMode()?<nav><h2>Connected test workspace</h2><p>Only synthetic authorized records are shown.</p><a href="/integration/members">Test members</a></nav>:undefined} companion={odooTestMode()?<ConnectedCompanion />:undefined}>{children}</AdaptiveShell>')
change('src/components/shell/AdaptiveShell.tsx','  companion?: ReactNode;','  companion?: ReactNode;\n  connectedTest?: boolean;')
change('src/components/shell/AdaptiveShell.tsx','({ children, context, companion }: Props)','({ children, context, companion, connectedTest = false }: Props)')
change('src/components/shell/AdaptiveShell.tsx','<Rail />','{connectedTest ? <nav><a href="/integration/members">Members</a></nav> : <Rail />}')
change('src/components/shell/AdaptiveShell.tsx','<ContextPane>{context}</ContextPane>','{context ?? <ContextPane>{undefined}</ContextPane>}')
change('src/components/shell/AdaptiveShell.tsx','<CompanionRail>{companion}</CompanionRail>','{companion ?? <CompanionRail />}')
change('src/components/shell/AdaptiveShell.tsx','<WorkspaceTabs />','{!connectedTest && <WorkspaceTabs />}')
change('src/components/shell/AdaptiveShell.tsx','<AdaptiveBottomNav />','{connectedTest ? <a href="/integration/members">Members</a> : <AdaptiveBottomNav />}')
# Never include generated private fixtures or build output in a review commit.
p=root/'.gitignore';p.write_text(p.read_text()+'\n.integration-build/\nplaywright-report/\ntest-results/\n.env.local\n')
