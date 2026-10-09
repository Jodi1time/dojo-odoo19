import Member360View from "@/components/people/member-360/Member360View";
import {getMember,getMemberAttendance} from "@/integrations/member";
import type {Member,MemberAttendance} from "@/domain/member";
import {notFound} from "next/navigation";
import {headers} from "next/headers";
import {odooRoute,odooTestMode} from "@/server/odoo-runtime";
export const dynamic="force-dynamic";
export default async function MemberPage({params}:{params:Promise<{id:string}>}){const {id}=await params;if(odooTestMode()){const h=await headers();const r=await odooRoute(new Request("http://internal.invalid",{headers:{cookie:h.get("cookie")||""}}),"member",id);if(r.status===404)notFound();if(!r.ok)return <section><h1>Member information unavailable</h1><a href="/integration/pair">Connect staff device</a></section>;const data=await r.json() as {member:Member;attendance:MemberAttendance};return <><p role="status">Connected to Odoo test data. Unconnected actions are disabled.</p><a href={`/integration/companion?memberId=${id}`}>Open guided Companion</a><Member360View member={data.member} attendance={data.attendance} liveTest /></>;}const member=await getMember(id);if(!member)notFound();return <Member360View member={member} attendance={await getMemberAttendance(member.id)} />;}
