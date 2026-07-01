import { Geist, Geist_Mono } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";
import "@livekit/components-styles";
import Providers from "./providers";

const siteUrl = "https://redocx.app";
const homepageUrl = `${siteUrl}/`;
const siteName = "ReDOCX";
const siteTitle = "ReDOCX | AI Document Automation, Compliance & E-Signatures";
const siteDescription =
  "ReDOCX helps professionals and teams automate compliance, e-signatures, redaction, data masking, PDF workflows, speech-to-text, text-to-speech, messaging and video calls.";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const structuredData = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${siteUrl}/#organization`,
      name: siteName,
      url: siteUrl,
      logo: {
        "@type": "ImageObject",
        url: `${siteUrl}/redocx-logo.svg`,
      },
      brand: {
        "@type": "Brand",
        name: siteName,
        alternateName: ["redocx", "redocx.app"],
      },
      description:
        "ReDOCX builds document automation software for compliance, electronic signatures, document security, PDF workflows, speech automation, and team collaboration.",
    },
    {
      "@type": "WebSite",
      "@id": `${siteUrl}/#website`,
      name: siteName,
      alternateName: ["redocx", "redocx.app"],
      url: homepageUrl,
      description: siteDescription,
      publisher: {
        "@id": `${siteUrl}/#organization`,
      },
      inLanguage: ["en", "fr"],
    },
    {
      "@type": "WebPage",
      "@id": `${homepageUrl}#webpage`,
      url: homepageUrl,
      name: siteTitle,
      description: siteDescription,
      isPartOf: {
        "@id": `${siteUrl}/#website`,
      },
      about: {
        "@id": `${siteUrl}/#software`,
      },
      primaryImageOfPage: {
        "@type": "ImageObject",
        url: `${siteUrl}/redocx-logo.svg`,
      },
      inLanguage: ["en", "fr"],
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${siteUrl}/#software`,
      name: siteName,
      alternateName: ["redocx", "redocx.app"],
      url: homepageUrl,
      mainEntityOfPage: {
        "@id": `${homepageUrl}#webpage`,
      },
      applicationCategory: "ProductivityApplication",
      applicationSubCategory: "Document automation platform",
      operatingSystem: "Web",
      browserRequirements: "Requires a modern web browser.",
      description:
        "ReDOCX is a document automation platform for creating, editing, translating, signing, securing, and managing documents. Core capabilities include compliance workflows, electronic signatures, redaction, data masking, PDF tools, speech-to-text, text-to-speech, and secure Business and Enterprise team collaboration with messaging and video calling.",
      featureList: [
        "Compliance workflows",
        "Electronic signatures",
        "Redaction",
        "Data masking",
        "PDF tools",
        "Speech-to-text transcription",
        "Text-to-speech generation",
        "Business and Enterprise team messaging",
        "Business and Enterprise video calling",
        "Project and team collaboration",
      ],
      audience: [
        {
          "@type": "BusinessAudience",
          audienceType: "Business and enterprise teams",
        },
        {
          "@type": "Audience",
          audienceType: "Professionals handling sensitive documents",
        },
      ],
      publisher: {
        "@id": `${siteUrl}/#organization`,
      },
      provider: {
        "@id": `${siteUrl}/#organization`,
      },
    },
  ],
};

function serializeJsonLd(data) {
  return JSON.stringify(data).replace(/</g, "\u003c");
}

export const metadata = {
  metadataBase: new URL(siteUrl),
  applicationName: siteName,
  title: {
    default: siteTitle,
    template: `%s | ${siteName}`,
  },
  description: siteDescription,
  keywords: [
    "ReDOCX",
    "redocx",
    "redocx.app",
    "document automation",
    "document automation platform",
    "document compliance software",
    "electronic signatures",
    "e-signature platform",
    "document redaction",
    "data masking",
    "PDF tools",
    "speech to text",
    "text to speech",
    "team messaging",
    "video conferencing",
    "business document workflows",
    "enterprise document automation",
  ],
  authors: [{ name: siteName }],
  creator: siteName,
  publisher: siteName,
  category: "business software",
  alternates: {
    canonical: "/",
    languages: {
      en: "/",
      fr: "/?lang=fr",
    },
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName,
    title: siteTitle,
    description: siteDescription,
    locale: "en_US",
    alternateLocale: ["fr_FR"],
  },
  twitter: {
    card: "summary",
    title: siteTitle,
    description: siteDescription,
  },
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/redocx-logo.svg", type: "image/svg+xml" },
      { url: "/redocx-icon-512.png", type: "image/png", sizes: "512x512" },
    ],
    shortcut: "/favicon.ico",
    apple: [
      {
        url: "/redocx-apple-icon.png",
        type: "image/png",
        sizes: "180x180",
      },
    ],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  formatDetection: {
    telephone: false,
    address: false,
    email: false,
  },
  appleWebApp: {
    title: siteName,
    capable: true,
    statusBarStyle: "black-translucent",
  },
};

function normalizeLanguage(value) {
  return value === "fr" ? "fr" : "en";
}

const ACCOUNT_EXIT_COOKIE_NAME = "redocx-account-exit";

export default async function RootLayout({ children }) {
  const cookieStore = await cookies();
  const language = normalizeLanguage(
    cookieStore.get("homepage-language")?.value,
  );
  const initialAccountExit = Boolean(
    cookieStore.get(ACCOUNT_EXIT_COOKIE_NAME)?.value,
  );

  return (
    <html lang={language} suppressHydrationWarning>
      <head>
        <script
          id="redocx-structured-data"
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: serializeJsonLd(structuredData),
          }}
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers
          initialLanguage={language}
          initialAccountExit={initialAccountExit}
        >
          {children}
        </Providers>
      </body>
    </html>
  );
}
