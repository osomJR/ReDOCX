import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

async function getRequiredAccessToken() {
  try {
    const session = await auth0.getSession();

    if (!session) {
      return {
        error: NextResponse.json(
          {
            detail: {
              error: "authorization_required",
              message: "You must be signed in.",
            },
          },
          { status: 401 },
        ),
      };
    }

    const tokenSet = await auth0.getAccessToken();
    const accessToken =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";

    if (!accessToken) {
      return {
        error: NextResponse.json(
          {
            detail: {
              error: "authorization_required",
              message: "Could not load access token.",
            },
          },
          { status: 401 },
        ),
      };
    }

    return { accessToken };
  } catch {
    return {
      error: NextResponse.json(
        {
          detail: {
            error: "authorization_required",
            message: "Could not load session.",
          },
        },
        { status: 401 },
      ),
    };
  }
}

async function readJsonBody(req) {
  try {
    return await req.json();
  } catch {
    return {};
  }
}

function withSearchParams(req, backendPath) {
  const url = new URL(req.url);
  const search = url.searchParams.toString();

  return search ? `${backendPath}?${search}` : backendPath;
}

async function proxyJsonToBackend({ backendPath, method = "GET", body }) {
  const tokenResult = await getRequiredAccessToken();

  if (tokenResult.error) {
    return tokenResult.error;
  }

  const headers = {
    Authorization: `Bearer ${tokenResult.accessToken}`,
  };

  const fetchOptions = {
    method,
    headers,
    cache: "no-store",
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(body);
  }

  const backendRes = await fetch(
    `${process.env.BACKEND_URL}${backendPath}`,
    fetchOptions,
  );

  const contentType = backendRes.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await backendRes.json()
    : { detail: { message: await backendRes.text() } };

  return NextResponse.json(data, { status: backendRes.status });
}

export async function POST(req, context) {
  const { conversationId } = await context.params;
  const body = await readJsonBody(req);

  return proxyJsonToBackend({
    backendPath: `/api/v1/conversations/${encodeURIComponent(conversationId)}/calls`,
    method: "POST",
    body,
  });
}
