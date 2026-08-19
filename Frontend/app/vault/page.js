"use client";

import { useAccount } from "@/components/account_provider";

export default function VaultPage() {
  const { user, authChecked } = useAccount();
  const storageIdeas = [
    "Secure files and documents",
    "Private notes and reference material",
    "Sensitive client or team information",
  ];

  if (!authChecked) {
    return (
      <main className="app-shell min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
        <section className="mx-auto flex min-h-[calc(100vh-6rem)] max-w-5xl items-center justify-center">
          <div className="rounded-3xl border app-surface-strong px-5 py-4 text-sm app-text-muted shadow-2xl">
            Checking account...
          </div>
        </section>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="app-shell min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
        <section className="mx-auto flex min-h-[calc(100vh-6rem)] max-w-5xl items-center">
          <div className="w-full rounded-3xl border app-surface-strong p-8 shadow-2xl md:p-10">
            <div className="inline-flex rounded-full border border-[var(--app-border)] px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
              Sign in required
            </div>
            <h1 className="mt-6 text-3xl font-semibold tracking-tight app-text sm:text-4xl">
              Vault
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 app-text-muted">
              Vault is available to signed-in ReDOCX users. Sign in to continue.
            </p>
            <a
              href="/auth/login?returnTo=/vault"
              className="mt-6 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
            >
              Sign in
            </a>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
      <section className="mx-auto flex min-h-[calc(100vh-6rem)] max-w-5xl items-center">
        <div className="w-full rounded-3xl border app-surface-strong p-8 shadow-2xl md:p-10">
          <div className="inline-flex rounded-full border border-[var(--app-border)] px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
            Vault preview
          </div>

          <div className="mt-6 grid gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight app-text sm:text-4xl">
                Vault
              </h1>
              <p className="mt-4 max-w-2xl text-base leading-7 app-text-muted">
                Store anything safely and securely in your ReDOCX Vault. This UI is ready for signed-in users while the secure storage backend is being built.
              </p>

              <div className="mt-8 grid gap-3">
                {storageIdeas.map((item) => (
                  <div
                    key={item}
                    className="rounded-2xl border app-surface px-4 py-3 text-sm font-medium app-text"
                  >
                    {item}
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-3xl border app-surface p-6">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl border app-surface-strong text-2xl">
                🔐
              </div>
              <h2 className="mt-5 text-xl font-semibold app-text">
                Secure storage coming next
              </h2>
              <p className="mt-3 text-sm leading-6 app-text-muted">
                Connect the backend later to support uploads, encrypted storage, folder organization, access controls, and retrieval.
              </p>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
