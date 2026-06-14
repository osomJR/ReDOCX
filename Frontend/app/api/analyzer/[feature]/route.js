import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
const ALLOWED_FEATURES = [
  "convert",
  "summarize",
  "grammar-correct",
  "translate",
  "transcribe",
  "explain",
  "generate-questions",
  "generate-answers",
  "redact",
  "data-mask",
  "compliance",
  "structured-extraction",
  "e-signature",
];

export async function GET() {
  return NextResponse.json({
    ok: true,
    message: "Analyzer proxy route is working. Send a POST request.",
  });
}

export async function POST(req, context) {
  const { feature } = await context.params;

  if (!ALLOWED_FEATURES.includes(feature)) {
    return NextResponse.json(
      {
        detail: {
          error: "invalid_feature",
          message: "Unsupported analyzer feature.",
        },
      },
      { status: 400 },
    );
  }

  const incomingFormData = await req.formData();
  const outboundFormData = new FormData();

  for (const [key, value] of incomingFormData.entries()) {
    if (
      typeof value === "object" &&
      value !== null &&
      typeof value.arrayBuffer === "function" &&
      typeof value.name === "string"
    ) {
      const buffer = await value.arrayBuffer();

      const fileBlob = new Blob([buffer], {
        type: value.type || "application/octet-stream",
      });

      outboundFormData.append(key, fileBlob, value.name);
    } else {
      outboundFormData.append(key, value);
    }
  }

  let accessToken = "";
  try {
    const session = await auth0.getSession();

    if (session) {
      const tokenSet = await auth0.getAccessToken();
      accessToken =
        typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    }
  } catch {
    accessToken = "";
  }

  const headers = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const backendRes = await fetch(
    `${process.env.BACKEND_URL}/api/v1/analyzer/${feature}`,
    {
      method: "POST",
      headers,
      body: outboundFormData,
    },
  );

  const contentType = backendRes.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await backendRes.json()
    : { detail: { message: await backendRes.text() } };

  return NextResponse.json(data, { status: backendRes.status });
}