export default function TermsOfUsePage() {
  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)]">
      <article className="mx-auto max-w-3xl rounded-3xl border app-surface-strong p-8 shadow-xl">
        <p className="text-sm font-semibold uppercase tracking-[0.16em] app-text-soft">
          ReDOCX Help
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight app-text">
          Terms of Use
        </h1>
        <p className="mt-4 leading-7 app-text-muted">
          Add your official Terms of Use content here. This page is linked from
          Settings → Help → Terms of Use in the user profile menu.
        </p>
      </article>
    </main>
  );
}
