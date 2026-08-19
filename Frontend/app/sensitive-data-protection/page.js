"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, EyeClosed, EyeOff, Loader2 } from "lucide-react";
import ActionCard from "@/components/ActionCard";
import AppSidebarLayout from "@/components/app_sidebar";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { sensitiveDataProtectionPageTranslations } from "@/lib/translations";

const actionIcons = {
  redact: EyeOff,
  mask: EyeClosed,
};

export default function SensitiveDataProtectionPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();

  const t = useMemo(
    () =>
      sensitiveDataProtectionPageTranslations[language] ||
      sensitiveDataProtectionPageTranslations.en,
    [language],
  );

  const actions = useMemo(
    () =>
      (t.actions || []).map((action) => ({
        ...action,
        icon: actionIcons[action.key] || EyeOff,
      })),
    [t],
  );

  if (!authChecked) {
    return (
      <AppSidebarLayout>
        <main className="app-page flex min-h-screen items-center justify-center px-6 app-text">
          <div className="inline-flex items-center gap-3 rounded-3xl border app-surface-strong px-5 py-4 text-sm app-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t.loading}
          </div>
        </main>
      </AppSidebarLayout>
    );
  }

  if (!user) {
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
          <button
            type="button"
            onClick={() => router.back()}
            className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted transition hover:app-text"
          >
            <ArrowLeft className="h-4 w-4" />
            {t.back}
          </button>

          <section className="mx-auto max-w-2xl rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6 shadow-2xl">
            <h1 className="text-xl font-semibold app-text">{t.signInTitle}</h1>
            <p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p>
            <a
              href="/auth/login?returnTo=/sensitive-data-protection"
              className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
            >
              {t.signIn}
            </a>
          </section>
        </main>
      </AppSidebarLayout>
    );
  }

  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
        <button
          type="button"
          onClick={() => router.back()}
          className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted transition hover:app-text"
        >
          <ArrowLeft className="h-4 w-4" />
          {t.back}
        </button>

        <section className="mb-8 rounded-3xl border app-surface-strong p-6 shadow-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
            {t.badge}
          </p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight app-text md:text-4xl">
            {t.title}
          </h1>
          <p className="mt-3 max-w-3xl app-text-muted">{t.description}</p>
        </section>

        <section className="grid gap-5 sm:grid-cols-2">
          {actions.map((action) => (
            <ActionCard
              key={`${language}-${action.key}`}
              action={action}
              onClick={() => router.push(action.route)}
            />
          ))}
        </section>
      </main>
    </AppSidebarLayout>
  );
}
