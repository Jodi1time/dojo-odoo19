import {odooRoute} from "@/server/odoo-runtime";
export const dynamic="force-dynamic";
export function GET(r:Request){return odooRoute(r,"members");}
