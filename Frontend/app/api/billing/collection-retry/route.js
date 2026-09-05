import { proxyBillingRequest } from "@/lib/billing_proxy";
export const POST = request => proxyBillingRequest(request, "/api/v1/billing/collection-retry");
