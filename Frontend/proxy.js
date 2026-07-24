import { NextResponse } from "next/server";
import { auth0 } from "./lib/auth0";

const BACKEND_BASE_URL =
  process.env.BACKEND_URL ||
  process.env.BACKEND_BASE_URL ||
  process.env.BACKEND_API_URL ||
  process.env.API_BASE_URL ||
  "http://localhost:8000";

const BACKEND_API_PREFIXES = [
  // Analyzer routes are intentionally handled by Next API routes so they can
  // bridge the Auth0 web session into a backend Bearer token. Do not rewrite
  // general /api/analyzer or /api/v1/analyzer traffic here.
  "/api/organizations",
  "/api/conversations",
  "/api/calls",
  "/api/billing",
  "/api/account/push-subscriptions",
];

// The compression page already supplies an Auth0 Bearer token when polling.
// Route this read-only endpoint directly to FastAPI because the generic Next
// analyzer bridge handles POST requests only and otherwise returns HTTP 405.
const BACKEND_AUTHENTICATED_READ_PREFIXES = [
  "/api/analyzer/pdf/compress/jobs",
];

const LEGACY_ANALYZER_ARTIFACT_PREFIX = "/api/v1/analyzer/artifacts";

function matchesPrefix(pathname, prefixes) {
  return prefixes.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function isNextRouteOwnedBackendPath(pathname) {
  // Attachment upload and download routes are owned by dedicated Node handlers
  // that bridge the Auth0 web session into a backend bearer token.
  const isAttachmentUpload =
    /^\/api\/conversations\/[1-9][0-9]*\/attachments\/?$/u.test(pathname);
  const isAttachmentDownload =
    /^\/api\/conversations\/[1-9][0-9]*\/messages\/[1-9][0-9]*\/attachments\/[1-9][0-9]*\/download\/?$/u.test(
      pathname,
    );
  // The service worker cannot read the browser's in-memory bearer token. Keep
  // the decline action on the dedicated Next route so it can bridge the Auth0
  // web session into the backend request after an offline push action.
  const isCallDecline =
    /^\/api\/calls\/[1-9][0-9]*\/decline\/?$/u.test(pathname);
  return isAttachmentUpload || isAttachmentDownload || isCallDecline;
}

function shouldProxyToBackend(request) {
  const pathname = request.nextUrl.pathname;
  if (isNextRouteOwnedBackendPath(pathname)) return false;
  if (matchesPrefix(pathname, BACKEND_API_PREFIXES)) return true;

  return (
    (request.method === "GET" || request.method === "HEAD") &&
    matchesPrefix(pathname, BACKEND_AUTHENTICATED_READ_PREFIXES)
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

function buildAnalyzerArtifactBridgeUrl(request) {
  const bridgeUrl = request.nextUrl.clone();
  bridgeUrl.pathname = bridgeUrl.pathname.replace(
    /^\/api\/v1\/analyzer\/artifacts(?=\/|$)/u,
    "/api/analyzer/artifacts",
  );
  return bridgeUrl;
}

export default async function proxy(request) {
  const pathname = request.nextUrl.pathname;

  // Older API responses and cached clients may still request the FastAPI path
  // directly. Keep those requests same-origin and route them through the
  // dedicated Next.js handler that injects the Auth0 access token.
  if (
    (request.method === "GET" || request.method === "HEAD") &&
    matchesPrefix(pathname, [LEGACY_ANALYZER_ARTIFACT_PREFIX])
  ) {
    return NextResponse.rewrite(buildAnalyzerArtifactBridgeUrl(request));
  }

  if (shouldProxyToBackend(request)) {
    return NextResponse.rewrite(buildBackendUrl(request));
  }

  return await auth0.middleware(request);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
  ],
};
