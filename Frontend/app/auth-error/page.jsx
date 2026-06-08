export default function AuthErrorPage({ searchParams }) {
  const code = searchParams?.code || "No code";
  const message = searchParams?.message || "No message";

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
        {JSON.stringify({ code, message }, null, 2)}
      </pre>

      <a href="/auth/login?returnTo=/">Back to sign in</a>
    </main>
  );
}
