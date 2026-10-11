// PROVISIONAL: our own path and shape until the real AI service has a contract
import { getContextFor } from "@/integrations/mock/companion";
import { mockRoute } from "@/integrations/mock/companion-http";
import { getStore } from "@/integrations/mock/companion-store";

// What the Companion shows for one page (CompanionContext).
// ?memberId=5001 for a member's page, no memberId for the front desk.
export function GET(request: Request) {
  const memberId = new URL(request.url).searchParams.get("memberId");

  return mockRoute(() => {
    const context = getContextFor(memberId);
    const ids = new Set(context.suggestions.map((task) => task.id));
    if (context.featured) ids.add(context.featured.suggestion.id);
    const receipts = [...getStore().usedKeys.values()]
      .map((item) => item.receipt)
      .filter((receipt) => ids.has(receipt.suggestionId));
    return Response.json({ ...context, receipts });
  });
}
