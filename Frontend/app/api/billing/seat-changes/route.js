import { proxyBillingRequest } from "@/lib/billing_proxy";
export const GET = request => proxyBillingRequest(request, "/api/v1/billing/seat-changes");
export const POST = request => proxyBillingRequest(request, "/api/v1/billing/seat-changes");
