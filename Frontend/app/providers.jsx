"use client";

import { LanguageProvider } from "@/components/language_provider";
import { ThemeProvider } from "@/components/theme_provider";
import { AccountProvider } from "@/components/account_provider";
import TeamRealtimeProvider from "@/components/team_realtime_provider";

export default function Providers({ children, initialLanguage }) {
  return (
    <LanguageProvider initialLanguage={initialLanguage}>
      <AccountProvider>
        <ThemeProvider>
          <TeamRealtimeProvider>{children}</TeamRealtimeProvider>
        </ThemeProvider>
      </AccountProvider>
    </LanguageProvider>
  );
}
