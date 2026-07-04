import { NextResponse } from "next/server";
import { auth0 } from "./lib/auth0";

const BACKEND_BASE_URL =
  process.env.BACKEND_URL ||
  process.env.BACKEND_BASE_URL ||
  process.env.BACKEND_API_URL ||
  process.env.API_BASE_URL ||
  "http://localhost:8000";

const BACKEND_API_PREFIXES = [
  "/api/v1/analyzer",
  "/api/analyzer",
  "/api/organizations",
  "/api/conversations",
  "/api/calls",
  "/api/billing",
];

function shouldProxyToBackend(pathname) {
  return BACKEND_API_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function buildBackendUrl(request) {
  const backendUrl = new URL(BACKEND_BASE_URL);
  const pathname = request.nextUrl.pathname;

  backendUrl.pathname = pathname.startsWith("/api/v1/")
    ? pathname
    : `/api/v1${pathname.replace(/^\/api(?=\/|$)/, "")}`;
  backendUrl.search = request.nextUrl.search;

  return backendUrl;
}

export default async function proxy(request) {
  if (shouldProxyToBackend(request.nextUrl.pathname)) {
    return NextResponse.rewrite(buildBackendUrl(request));
  }

  return await auth0.middleware(request);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
  ],
};
