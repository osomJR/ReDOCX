"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  CreditCard,
  Loader2,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from "lucide-react";
import AppSidebarLayout from "@/components/app_sidebar";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { getAccessToken } from "@/lib/api_client";
import { billingPageTranslations } from "@/lib/translations";

const PLAN_ICON_MAP = {
  free: Sparkles,
  personal: CreditCard,
  business: UsersRound,
  enterprise: ShieldCheck,
};

const PLAN_ORDER = ["free", "personal", "business", "enterprise"];


const CHECKOUT_PROVIDER_ORDER = ["paystack", "stripe"];

const AFRICAN_COUNTRY_CODES = new Set([
  "DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG",
  "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN",
  "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ",
  "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD",
  "TZ", "TG", "TN", "UG", "ZM", "ZW",
]);

const STRIPE_COUNTRY_CODES = new Set([
  "US", "CA", "GB", "IE", "FR", "DE", "ES", "IT", "NL", "BE", "PT", "AT",
  "CH", "SE", "NO", "DK", "FI", "PL", "CZ", "GR", "RO", "BG", "HR", "HU",
  "LU", "LT", "LV", "EE", "SK", "SI", "CY", "MT",
]);

const PROVIDER_FALLBACK_COPY = {
  en: {
    title: "Choose payment provider",
    description:
      "Paystack is recommended for Nigerian and African users. Stripe is recommended for US and European users. You can choose either provider before upgrading.",
    recommended: "Recommended",
    selected: "Selected",
    configured: "Ready",
    notConfigured: "Not configured",
    unavailableForPlan: "This provider is not configured for this plan yet.",
    checkoutWith: "Checkout with {provider}",
    paystack: {
      name: "Paystack",
      summary: "Nigeria / Africa cards, bank transfer, USSD, and local rails.",
      region_label: "Nigeria / Africa",
    },
    stripe: {
      name: "Stripe",
      summary: "US / Europe cards and international checkout.",
      region_label: "US / Europe",
    },
  },
  fr: {
    title: "Choisir le fournisseur de paiement",
    description:
      "Paystack est recommandé pour les utilisateurs nigérians et africains. Stripe est recommandé pour les États-Unis et l’Europe. Vous pouvez choisir le fournisseur avant la mise à niveau.",
    recommended: "Recommandé",
    selected: "Sélectionné",
    configured: "Prêt",
    notConfigured: "Non configuré",
    unavailableForPlan: "Ce fournisseur n’est pas encore configuré pour ce forfait.",
    checkoutWith: "Paiement avec {provider}",
    paystack: {
      name: "Paystack",
      summary: "Cartes Nigeria / Afrique, virement bancaire, USSD et moyens locaux.",
      region_label: "Nigeria / Afrique",
    },
    stripe: {
      name: "Stripe",
      summary: "Cartes États-Unis / Europe et paiement international.",
      region_label: "États-Unis / Europe",
    },
  },
};

const FALLBACK_PLAN_COPY = {
  en: {
    apiMissing:
      "Billing API route is not connected yet. Showing a local plan preview for now.",
    checkoutComingSoon: "Checkout is not connected yet.",
    currentPlanReason: "This is your current plan.",
    plans: {
      free: {
        name: "Free",
        summary: "Start using core ReDOCX tools with limited monthly usage.",
        price_label: "$0",
        billing_period: "Monthly",
        account_count_label: "1 account",
        features: [
          "Limited document processing",
          "Core AI document tools",
          "Basic PDF features",
        ],
      },
      personal: {
        name: "Personal",
        summary: "Higher limits for individual document workflows.",
        price_label: "Coming soon",
        billing_period: "Monthly",
        account_count_label: "1 account",
        features: [
          "More document processing",
          "Redaction and masking workflows",
          "Priority personal usage",
        ],
      },
      business: {
        name: "Business",
        summary: "Team plan for shared document work and collaboration.",
        price_label: "Coming soon",
        billing_period: "Monthly",
        account_count_label: "Team accounts",
        features: [
          "Team access",
          "Organization collaboration",
          "Business document workflows",
        ],
      },
      enterprise: {
        name: "Enterprise",
        summary: "Custom usage, support, and deployment options for larger teams.",
        price_label: "Custom",
        billing_period: "Annual",
        account_count_label: "Custom accounts",
        features: [
          "Custom limits",
          "Advanced support",
          "Enterprise controls",
        ],
      },
    },
  },
  fr: {
    apiMissing:
      "La route API de facturation n’est pas encore connectée. Affichage temporaire d’un aperçu local des forfaits.",
    checkoutComingSoon: "Le paiement n’est pas encore connecté.",
    currentPlanReason: "Ceci est votre forfait actuel.",
    plans: {
      free: {
        name: "Gratuit",
        summary: "Commencez avec les outils ReDOCX essentiels et une utilisation mensuelle limitée.",
        price_label: "0 $",
        billing_period: "Mensuel",
        account_count_label: "1 compte",
        features: [
          "Traitement de documents limité",
          "Outils IA essentiels",
          "Fonctions PDF de base",
        ],
      },
      personal: {
        name: "Personnel",
        summary: "Des limites plus élevées pour les flux de documents individuels.",
        price_label: "Bientôt",
        billing_period: "Mensuel",
        account_count_label: "1 compte",
        features: [
          "Plus de traitement de documents",
          "Flux de masquage et de rédaction",
          "Utilisation personnelle prioritaire",
        ],
      },
      business: {
        name: "Business",
        summary: "Forfait d’équipe pour le travail documentaire partagé.",
        price_label: "Bientôt",
        billing_period: "Mensuel",
        account_count_label: "Comptes d’équipe",
        features: [
          "Accès d’équipe",
          "Collaboration d’organisation",
          "Flux documentaires business",
        ],
      },
      enterprise: {
        name: "Enterprise",
        summary: "Options personnalisées d’utilisation, de support et de déploiement.",
        price_label: "Sur mesure",
        billing_period: "Annuel",
        account_count_label: "Comptes personnalisés",
        features: [
          "Limites personnalisées",
          "Support avancé",
          "Contrôles enterprise",
        ],
      },
    },
  },
};


function providerPageCopy(language, t) {
  return {
    ...(PROVIDER_FALLBACK_COPY[language] || PROVIDER_FALLBACK_COPY.en),
    ...(t.paymentProviders || {}),
  };
}

function defaultProviderOptions(language) {
  const copy = PROVIDER_FALLBACK_COPY[language] || PROVIDER_FALLBACK_COPY.en;
  return CHECKOUT_PROVIDER_ORDER.map((key) => ({
    key,
    name: copy[key].name,
    summary: copy[key].summary,
    region_label: copy[key].region_label,
    configured: false,
    recommended: key === "stripe",
  }));
}

function normalizeProviderKey(value) {
  const normalized = String(value || "").toLowerCase();
  return CHECKOUT_PROVIDER_ORDER.includes(normalized) ? normalized : "stripe";
}

function extractCountryCodes(...values) {
  const codes = [];

  for (const value of values) {
    if (typeof value !== "string") continue;
    const text = value.trim().replaceAll("_", "-");
    if (!text) continue;

    const parts = text.split("-").map((part) => part.trim().toUpperCase());
    const lastPart = parts[parts.length - 1];

    if (lastPart?.length === 2) codes.push(lastPart);
    if (text.length === 2) codes.push(text.toUpperCase());
  }

  return codes;
}

function getClientRegionHint({ user, language }) {
  if (typeof window === "undefined") return language;

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  const navLanguage = window.navigator?.language || "";
  const navLanguages = Array.isArray(window.navigator?.languages)
    ? window.navigator.languages.join("|")
    : "";

  return [
    timeZone,
    navLanguage,
    navLanguages,
    language,
    user?.locale,
    user?.lang,
    user?.country,
    user?.country_code,
  ]
    .filter(Boolean)
    .join("|");
}

function detectClientRecommendedProvider({ user, language }) {
  if (typeof window === "undefined") return null;

  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  const navLanguage = window.navigator?.language || "";
  const navLanguages = Array.isArray(window.navigator?.languages)
    ? window.navigator.languages
    : [];
  const values = [
    timeZone,
    navLanguage,
    ...navLanguages,
    language,
    user?.locale,
    user?.lang,
    user?.country,
    user?.country_code,
  ].filter(Boolean);
  const normalizedText = values.join(" ").toLowerCase();
  const countryCodes = extractCountryCodes(...values);

  if (
    timeZone.startsWith("Africa/") ||
    normalizedText.includes("africa") ||
    countryCodes.some((code) => AFRICAN_COUNTRY_CODES.has(code))
  ) {
    return "paystack";
  }

  if (
    timeZone.startsWith("Europe/") ||
    timeZone.startsWith("America/") ||
    normalizedText.includes("europe") ||
    countryCodes.some((code) => STRIPE_COUNTRY_CODES.has(code))
  ) {
    return "stripe";
  }

  return null;
}

function resolveSelectedProvider({ billingState, user, language, currentProvider }) {
  const providerKeys = new Set(
    (billingState?.providers || []).map((provider) => normalizeProviderKey(provider.key)),
  );
  const clientRecommended = detectClientRecommendedProvider({ user, language });
  const backendRecommended = normalizeProviderKey(billingState?.recommended_provider || billingState?.provider);
  const existing = currentProvider ? normalizeProviderKey(currentProvider) : "";

  for (const candidate of [clientRecommended, existing, backendRecommended, "stripe", "paystack"]) {
    if (candidate && providerKeys.has(candidate)) return candidate;
  }

  return "stripe";
}

async function readBillingJson(response) {
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : { detail: { message: await response.text().catch(() => "Request failed.") } };

  if (!response.ok) {
    const error = new Error(
      payload?.detail?.message ||
        payload?.detail?.error ||
        payload?.message ||
        "Request failed.",
    );
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

async function billingFetch(path, options = {}) {
  const token = await getAccessToken();
  return fetch(path, {
    ...options,
    credentials: "include",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
}

async function getBillingPlans() {
  const response = await billingFetch("/api/billing/plans");
  return readBillingJson(response);
}

async function createBillingUpgradeIntent(targetPlan, { provider, regionHint } = {}) {
  const response = await billingFetch("/api/billing/upgrade-intents", {
    method: "POST",
    body: JSON.stringify({
      target_plan: targetPlan,
      provider,
      region_hint: regionHint,
    }),
  });
  return readBillingJson(response);
}

function normalizePlanKey(value) {
  const normalized = String(value || "").toLowerCase();
  return PLAN_ORDER.includes(normalized) ? normalized : "free";
}

function buildFallbackBillingState({ language, entitlement }) {
  const copy = FALLBACK_PLAN_COPY[language] || FALLBACK_PLAN_COPY.en;
  const currentPlanKey = normalizePlanKey(entitlement?.plan);

  const plans = PLAN_ORDER.map((key) => {
    const planCopy = copy.plans[key];
    const isCurrent = key === currentPlanKey;

    return {
      key,
      ...planCopy,
      is_current: isCurrent,
      can_upgrade: false,
      checkout_configured: false,
      provider_checkout_configured: {
        paystack: false,
        stripe: false,
      },
      reason: isCurrent ? copy.currentPlanReason : copy.checkoutComingSoon,
    };
  });

  return {
    current_plan: currentPlanKey,
    current_plan_name:
      plans.find((plan) => plan.is_current)?.name || copy.plans.free.name,
    provider: "stripe",
    recommended_provider: "stripe",
    providers: defaultProviderOptions(language),
    plans,
  };
}

function isNotFoundError(error) {
  return error?.status === 404;
}

function getErrorMessage(error, fallback) {
  return (
    error?.payload?.detail?.message ||
    error?.payload?.detail?.error ||
    error?.payload?.message ||
    error?.message ||
    fallback
  );
}

function PlanCard({ plan, t, providerCopy, selectedProvider, busyPlan, onUpgrade }) {
  const Icon = PLAN_ICON_MAP[plan.key] || CreditCard;
  const isBusy = busyPlan === plan.key;
  const providerConfigured = Boolean(
    plan.provider_checkout_configured?.[selectedProvider] ?? plan.checkout_configured,
  );
  const providerName =
    providerCopy?.[selectedProvider]?.name ||
    selectedProvider?.replace(/^./, (letter) => letter.toUpperCase()) ||
    "checkout";
  const buttonLabel = providerCopy.checkoutWith.replace("{provider}", providerName);

  return (
    <article
      className={`relative overflow-hidden rounded-3xl border p-6 shadow-sm transition md:p-7 ${
        plan.is_current
          ? "border-[var(--app-border-strong)] app-surface-strong"
          : "border-[var(--app-border)] app-surface"
      }`}
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.12),transparent_34%)]" />

      <div className="relative flex h-full flex-col">
        <div className="flex items-start justify-between gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border app-surface-strong">
            <Icon className="h-5 w-5 app-text-muted" />
          </div>

          {plan.is_current ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-3 py-1 text-xs font-semibold text-emerald-300">
              <CheckCircle2 className="h-3.5 w-3.5" />
              {t.currentPlan}
            </span>
          ) : null}
        </div>

        <div className="mt-6">
          <h2 className="text-2xl font-semibold tracking-tight app-text">
            {plan.name}
          </h2>
          <p className="mt-2 min-h-[3rem] text-sm leading-6 app-text-muted">
            {plan.summary}
          </p>
        </div>

        <div className="mt-5 rounded-2xl border border-[var(--app-border)] app-surface-strong p-4">
          <p className="text-2xl font-semibold app-text">{plan.price_label}</p>
          <p className="mt-1 text-xs app-text-soft">
            {plan.billing_period} · {plan.account_count_label}
          </p>
        </div>

        <ul className="mt-5 space-y-3 text-sm app-text-muted">
          {(plan.features || []).map((feature) => (
            <li key={feature} className="flex gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              <span>{feature}</span>
            </li>
          ))}
        </ul>

        <div className="mt-6 flex-1" />

        <p className="mt-6 min-h-[2.5rem] text-xs leading-5 app-text-soft">
          {plan.can_upgrade && !providerConfigured
            ? providerCopy.unavailableForPlan
            : plan.reason}
        </p>

        {plan.can_upgrade ? (
          <button
            type="button"
            onClick={() => onUpgrade(plan.key)}
            disabled={Boolean(busyPlan) || !providerConfigured}
            className="mt-4 inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isBusy ? t.creatingUpgrade : buttonLabel}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function ProviderSelector({ providers, selectedProvider, onSelect, providerCopy }) {
  if (!providers?.length) return null;

  return (
    <section className="mt-6 rounded-3xl border app-surface-strong p-5 shadow-sm md:p-6">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-lg font-semibold app-text">{providerCopy.title}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 app-text-muted">
            {providerCopy.description}
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {providers.map((provider) => {
          const key = normalizeProviderKey(provider.key);
          const providerFallback = providerCopy[key] || {};
          const selected = selectedProvider === key;
          const name = provider.name || providerFallback.name || key;
          const summary = provider.summary || providerFallback.summary;
          const regionLabel = provider.region_label || providerFallback.region_label;

          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelect(key)}
              className={`rounded-2xl border p-4 text-left transition hover:scale-[1.005] ${
                selected
                  ? "border-[var(--app-border-strong)] app-surface"
                  : "border-[var(--app-border)] app-surface-strong"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-base font-semibold app-text">{name}</p>
                  <p className="mt-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                    {regionLabel}
                  </p>
                </div>

                <div className="flex flex-col items-end gap-1">
                  {provider.recommended ? (
                    <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-300">
                      {providerCopy.recommended}
                    </span>
                  ) : null}
                  {selected ? (
                    <span className="rounded-full border border-[var(--app-border)] px-2.5 py-1 text-[11px] font-semibold app-text-muted">
                      {providerCopy.selected}
                    </span>
                  ) : null}
                </div>
              </div>

              <p className="mt-3 text-sm leading-6 app-text-muted">{summary}</p>
              <p className="mt-3 text-xs app-text-soft">
                {provider.configured ? providerCopy.configured : providerCopy.notConfigured}
              </p>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export default function BillingPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked, entitlement, reloadAccount } = useAccount();
  const t = useMemo(
    () => billingPageTranslations[language] || billingPageTranslations.en,
    [language],
  );
  const providerCopy = useMemo(
    () => providerPageCopy(language, t),
    [language, t],
  );

  const [billingState, setBillingState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyPlan, setBusyPlan] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("stripe");

  useEffect(() => {
    let cancelled = false;

    async function loadBilling() {
      if (!authChecked) return;

      if (!user) {
        setLoading(false);
        setBillingState(null);
        return;
      }

      setLoading(true);
      setError("");

      try {
        const data = await getBillingPlans();
        if (cancelled) return;
        setBillingState(data);
        setSelectedProvider((current) =>
          resolveSelectedProvider({
            billingState: data,
            user,
            language,
            currentProvider: current,
          }),
        );
      } catch (caught) {
        if (cancelled) return;

        if (isNotFoundError(caught)) {
          const fallbackState = buildFallbackBillingState({ language, entitlement });
          setBillingState(fallbackState);
          setSelectedProvider((current) =>
            resolveSelectedProvider({
              billingState: fallbackState,
              user,
              language,
              currentProvider: current,
            }),
          );
          setMessage(
            t.billingApiMissing ||
              FALLBACK_PLAN_COPY[language]?.apiMissing ||
              FALLBACK_PLAN_COPY.en.apiMissing,
          );
          return;
        }

        setError(getErrorMessage(caught, t.loadFailed));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadBilling();

    return () => {
      cancelled = true;
    };
  }, [authChecked, user, entitlement, language, t.loadFailed, t.billingApiMissing]);

  async function handleUpgrade(targetPlan) {
    const provider = normalizeProviderKey(selectedProvider);

    setBusyPlan(targetPlan);
    setError("");
    setMessage("");

    try {
      const data = await createBillingUpgradeIntent(targetPlan, {
        provider,
        regionHint: getClientRegionHint({ user, language }),
      });

      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      setMessage(data?.message || t.checkoutNotConfigured);
      await reloadAccount?.();
      const latest = await getBillingPlans();
      setBillingState(latest);
      setSelectedProvider((current) =>
        resolveSelectedProvider({
          billingState: latest,
          user,
          language,
          currentProvider: current || provider,
        }),
      );
    } catch (caught) {
      if (isNotFoundError(caught)) {
        setMessage(t.checkoutNotConfigured);
      } else {
        setError(getErrorMessage(caught, t.upgradeFailed));
      }
    } finally {
      setBusyPlan("");
    }
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 app-hero-overlay" />

        <div className="relative mx-auto max-w-7xl px-6 py-8 md:px-8 md:py-10">
          <button
            type="button"
            onClick={() => router.back()}
            className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted transition hover:app-text"
          >
            <ArrowLeft className="h-4 w-4" />
            {t.back}
          </button>

          <section className="rounded-3xl border app-surface-strong p-6 shadow-2xl md:p-8">
            <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
              <div className="max-w-3xl">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
                  {t.badge}
                </p>
                <h1 className="mt-3 text-3xl font-semibold tracking-tight app-text md:text-4xl">
                  {t.title}
                </h1>
                <p className="mt-3 text-sm leading-6 app-text-muted md:text-base">
                  {t.description}
                </p>
              </div>

              {billingState?.current_plan_name ? (
                <div className="rounded-2xl border border-[var(--app-border)] app-surface px-4 py-3 text-sm">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                    {t.yourPlan}
                  </p>
                  <p className="mt-1 text-lg font-semibold app-text">
                    {billingState.current_plan_name}
                  </p>
                </div>
              ) : null}
            </div>
          </section>

          {!authChecked || loading ? (
            <section className="mt-6 rounded-3xl border app-surface-strong p-6">
              <div className="inline-flex items-center gap-3 text-sm app-text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t.loading}
              </div>
            </section>
          ) : !user ? (
            <section className="mt-6 rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6">
              <h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2>
              <p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p>
              <a
                href="/auth/login?returnTo=/billing"
                className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]"
              >
                {t.signIn}
              </a>
            </section>
          ) : (
            <>
              {error ? (
                <div className="mt-6 flex items-start gap-3 rounded-3xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                  <span>{error}</span>
                </div>
              ) : null}

              {message ? (
                <div className="mt-6 flex items-start gap-3 rounded-3xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-100">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                  <span>{message}</span>
                </div>
              ) : null}

              <ProviderSelector
                providers={billingState?.providers || defaultProviderOptions(language)}
                selectedProvider={selectedProvider}
                onSelect={setSelectedProvider}
                providerCopy={providerCopy}
              />

              <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                {(billingState?.plans || []).map((plan) => (
                  <PlanCard
                    key={plan.key}
                    plan={plan}
                    t={t}
                    providerCopy={providerCopy}
                    selectedProvider={selectedProvider}
                    busyPlan={busyPlan}
                    onUpgrade={handleUpgrade}
                  />
                ))}
              </section>
            </>
          )}
        </div>
      </div>
    </AppSidebarLayout>
  );
}
