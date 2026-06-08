export const dynamic = "force-dynamic";

export default async function AuthErrorPage({ searchParams }) {
  const params = await searchParams;

  const code = params?.code || "No code";
  const message = params?.message || "No message";
  const cause = params?.cause || "No cause";

  return (
    <main style={{ padding: 24 }}>
      <h1>Authentication error</h1>

      <p>Something went wrong during sign-in.</p>

      <pre
        style={{
          marginTop: 16,
          padding: 16,
          background: "#111",
          color: "#fff",
          borderRadius: 8,
          whiteSpace: "pre-wrap",
        }}
      >
        {JSON.stringify({ code, message, cause }, null, 2)}
      </pre>

      <a href="/auth/login?returnTo=/">Back to sign in</a>
    </main>
  );
}
