import { bridgeDownload } from "@/lib/download_bridge";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(req) {
  const query = new URLSearchParams();
  const document = new URL(req.url).searchParams.get("document_id");
  if(document !== null) query.set("document_id",document);
  return bridgeDownload(req,"/api/v1/analyzer/e-signature/recipient/document",{signing:true,query,wantsInlinePreview:true});
}
