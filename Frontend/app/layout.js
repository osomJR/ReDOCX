import { Geist, Geist_Mono } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";
import "@livekit/components-styles";
import Providers from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata = {
  title: "ReDOCX",
  description: "",
};

function normalizeLanguage(value) {
  return value === "fr" ? "fr" : "en";
}

export default async function RootLayout({ children }) {
  const cookieStore = await cookies();
  const language = normalizeLanguage(
    cookieStore.get("homepage-language")?.value,
  );

  return (
    <html lang={language} suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers initialLanguage={language}>{children}</Providers>
      </body>
    </html>
  );
}