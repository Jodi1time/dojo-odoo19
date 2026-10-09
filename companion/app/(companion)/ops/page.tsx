import FrontDeskView from "@/components/operations/front-desk/FrontDeskView";
import { getMember } from "@/integrations/member";
import { odooTestMode } from "@/server/odoo-runtime";
import { redirect } from "next/navigation";
export const dynamic = "force-dynamic";
export default async function OpsPage() {
  if (odooTestMode()) redirect("/integration/members");
  const activeMember = await getMember("5001");
  return <FrontDeskView activeMember={activeMember} />;
}
