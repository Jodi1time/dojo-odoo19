import {odooRoute,odooTestMode} from "@/server/odoo-runtime";
import {GET as mockGET} from "@/integrations/mock/routes/roster";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export async function GET(r:Request,c:RouteContext<"/api/v2/sessions/[sessionId]/roster">){return odooTestMode()?odooRoute(r,"roster",(await c.params).sessionId):mockGET(r,c);}
