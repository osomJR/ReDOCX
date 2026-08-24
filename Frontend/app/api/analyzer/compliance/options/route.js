import { proxyComplianceRequest } from "../_proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req) {
  return proxyComplianceRequest(req, {
    backendPath: "/api/v1/analyzer/compliance/options",
    method: "GET",
  });
}
