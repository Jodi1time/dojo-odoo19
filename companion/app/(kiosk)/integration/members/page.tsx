import {headers} from "next/headers";
import {odooRoute} from "@/server/odoo-runtime";
export const dynamic="force-dynamic";
export default async function Members(){const h=await headers();const r=await odooRoute(new Request("http://internal.invalid",{headers:{cookie:h.get("cookie")||""}}),"members");if(!r.ok)return <main><h1>Staff access required</h1><a href="/integration/pair">Connect staff device</a></main>;const members=await r.json() as {id:string;name:string}[];return <main style={{padding:32}}><h1>Odoo test members</h1><p>Identity and attendance are read from the test database.</p>{members.map(m=><p key={m.id}><a href={`/people/${m.id}`}>{m.name}</a> · <a href={`/integration/companion?memberId=${m.id}`}>Guided Companion</a></p>)}</main>;}
