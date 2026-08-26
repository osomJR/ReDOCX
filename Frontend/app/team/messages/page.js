"use client";

import { useEffect } from "react";
import { useLanguage } from "@/components/language_provider";
import { legacyTeamMessagesPageTranslations } from "@/lib/translations";
import { useRouter, useSearchParams } from "next/navigation";

/**
 * Compatibility route for existing /team/messages bookmarks and realtime
 * notification links. The canonical collaboration workspace now lives at
 * /team, and all conversation/message/call deep-link parameters are retained.
 */
export default function LegacyTeamMessagesPage() {
  const { language } = useLanguage();
  const t =
    legacyTeamMessagesPageTranslations[language] ||
    legacyTeamMessagesPageTranslations.en;
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();

  useEffect(() => {
    router.replace(query ? `/team?${query}` : "/team");
  }, [query, router]);

  return (
    <main className="flex h-dvh items-center justify-center app-page px-4">
      <div className="rounded-2xl border app-surface-strong px-5 py-4 text-sm app-text-muted">
        {t.opening}
      </div>
    </main>
  );
}