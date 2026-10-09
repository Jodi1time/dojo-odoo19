import {odooRoute,odooTestMode} from "@/server/odoo-runtime";
import {GET as mockGET} from "@/integrations/mock/routes/sessions";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export function GET(r:Request){return odooTestMode()?odooRoute(r,"sessions"):mockGET();}
