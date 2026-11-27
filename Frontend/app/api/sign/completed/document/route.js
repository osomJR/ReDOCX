import { bridgeDownload } from "@/lib/download_bridge";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(req) {
  const query = new URLSearchParams();
  for(const key of ["artifact","document_id"]) {
    const value=new URL(req.url).searchParams.get(key); if(value!==null) query.set(key,value);
  }
  return bridgeDownload(req,"/api/v1/analyzer/e-signature/completed/document",{signing:true,query});
}
