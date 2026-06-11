"use client";

import { useLanguage } from "@/components/language_provider";
import { verifyEmailRequiredPageTranslations } from "@/lib/translations";

export default function VerifyEmailRequiredPage() {
  const { language } = useLanguage();
  const t =
    verifyEmailRequiredPageTranslations[language] ||
    verifyEmailRequiredPageTranslations.en;

  return (
    <main className="app-shell flex min-h-screen items-center justify-center px-6 py-16">
      <section className="w-full max-w-lg rounded-3xl border app-surface-strong p-8 text-center shadow-2xl">
        <p className="text-sm font-semibold uppercase tracking-[0.24em] app-text-soft">
          {t.badge}
        </p>

        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.03em] app-text">
          {t.title}
        </h1>

        <p className="mt-4 text-base leading-7 app-text-muted">
          {t.description}
        </p>

        <p className="mt-5 rounded-2xl border app-surface px-4 py-3 text-sm leading-6 app-text-soft">
          {t.nextStep}
        </p>
      </section>
    </main>
  );
}
