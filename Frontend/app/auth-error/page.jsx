export default function AuthErrorPage() {
  return (
    <main style={{ padding: 24 }}>
      <h1>Authentication error</h1>
      <p>Something went wrong during sign-in. Please try again.</p>
      <a href="/auth/login?returnTo=/">Back to sign in</a>
    </main>
  );
}
