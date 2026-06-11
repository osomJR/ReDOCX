"use client";

import { LanguageProvider } from "@/components/language_provider";
import { ThemeProvider } from "@/components/theme_provider";
import { AccountProvider } from "@/components/account_provider";
import TeamRealtimeProvider from "@/components/team_realtime_provider";

export default function Providers({ children, initialLanguage, initialAccountExit = false }) {
  return (
    <LanguageProvider initialLanguage={initialLanguage}>
      <AccountProvider initialAccountExit={initialAccountExit}>
        <ThemeProvider>
          <TeamRealtimeProvider>{children}</TeamRealtimeProvider>
        </ThemeProvider>
      </AccountProvider>
    </LanguageProvider>
  );
}
