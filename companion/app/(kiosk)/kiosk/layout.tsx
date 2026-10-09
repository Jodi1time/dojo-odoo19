import type {ReactNode} from "react";
import {headers} from "next/headers";
import {odooTestMode,stateRoute} from "@/server/odoo-runtime";
export const dynamic="force-dynamic";
export default async function KioskAccess({children}:{children:ReactNode}){
  if(odooTestMode()){
    const h=await headers();
    const state=stateRoute(new Request("http://internal.invalid",{headers:{cookie:h.get("cookie")||""}}));
    if(!state.ok)return <main role="status" style={{padding:32}}><h1>Kiosk unavailable</h1><p>Please ask a staff member to connect this kiosk.</p></main>;
  }
  return children;
}
