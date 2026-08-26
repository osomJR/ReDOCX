"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  FileArchive,
  FilePenLine,
  Files,
  FileStack,
  Loader2,
  Lock,
  ShieldCheck,
} from "lucide-react";
import ActionCard from "@/components/ActionCard";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  pdfToolsLockActionTranslations,
  pdfToolsPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";

const actionIcons = {
  combinePdf: Files,
  compressPdf: FileArchive,
  editPdf: FilePenLine,
  lockPdf: Lock,
  splitPdf: FileStack,
};
const lockPdfActionCopy = pdfToolsLockActionTranslations;

export default function PdfToolsPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { authChecked } = useAccount();

  const t = useMemo(
    () => pdfToolsPageTranslations[language] || pdfToolsPageTranslations.en,
    [language],
  );

  const lockPdfAction = useMemo(
    () => lockPdfActionCopy[language] || lockPdfActionCopy.en,
    [language],
  );

  const actions = useMemo(() => {
    const translatedActions = t.actions || [];
    const hasLockPdfAction = translatedActions.some(
      (action) => action.key === "lockPdf",
    );

    const orderedActions = hasLockPdfAction
      ? translatedActions
      : translatedActions.some((action) => action.key === "combinePdf")
        ? translatedActions.flatMap((action) =>
            action.key === "combinePdf" ? [action, lockPdfAction] : [action],
          )
        : [...translatedActions, lockPdfAction];

    return orderedActions.map((action) => ({
      ...action,
      icon: actionIcons[action.key] || Files,
      requiresAuth: !action.comingSoon,
    }));
  }, [t, lockPdfAction]);

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

          <div className="mt-5 grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl border app-surface px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                {t.freeQuota}
              </p>
              <p className="mt-1 text-sm app-text-muted">
                {t.quotaDescription}
              </p>
            </div>
            <div className="rounded-2xl border app-surface px-4 py-3">
              <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                <ShieldCheck className="h-4 w-4" />
                {t.paidUnlimited}
              </p>
              <p className="mt-1 text-sm app-text-muted">{t.description}</p>
            </div>
          </div>
        </section>

        <section className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
          {actions.map((action) => {
            const isLocked = action.comingSoon || !action.route;

            return (
              <ActionCard
                key={`${language}-${action.key}`}
                action={action}
                locked={isLocked}
                onClick={isLocked ? undefined : () => router.push(action.route)}
              />
            );
          })}
        </section>
      </main>
    </AppSidebarLayout>
  );
}
