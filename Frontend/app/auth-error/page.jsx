"use client";

import { useLanguage } from "@/components/language_provider";
import { buildAuthSignInUrl } from "@/lib/auth_urls";

const copy = {
  en: {
    title: "Authentication error",
    description: "Something went wrong during sign-in. Please try again.",
    back: "Back to sign in",
  },
  fr: {
    title: "Erreur d’authentification",
    description:
      "Une erreur s’est produite pendant la connexion. Veuillez réessayer.",
    back: "Retour à la connexion",
  },
};

export default function AuthErrorPage() {
  const { language } = useLanguage();
  const t = copy[language] || copy.en;

  return (
    <main className="app-shell flex min-h-screen items-center justify-center px-6 py-16">
      <section className="w-full max-w-lg rounded-3xl border app-surface-strong p-8 text-center shadow-2xl">
        <h1 className="text-3xl font-semibold tracking-[-0.03em] app-text">
          {t.title}
        </h1>

        <p className="mt-4 text-base leading-7 app-text-muted">
          {t.description}
        </p>

        <a
          href={buildAuthSignInUrl(language)}
          className="mt-6 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
        >
          {t.back}
        </a>
      </section>
    </main>
  );
}
