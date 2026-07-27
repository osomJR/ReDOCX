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
import {
  createBillingUpgradeIntent,
  getBillingPlans,
  manageBillingSubscription,
} from "@/lib/api_client";
import { buildAuthLoginUrl } from "@/lib/auth_urls";
import { billingPageTranslations } from "@/lib/translations";

const PLAN_ICON_MAP = {
  free: Sparkles,
  personal: CreditCard,
  business: UsersRound,
  enterprise: ShieldCheck,
};

const PLAN_ORDER = ["free", "personal", "business", "enterprise"];
const CHECKOUT_PROVIDER_ORDER = ["paystack", "stripe"];

const SUBSCRIPTION_MANAGEMENT_COPY = {
  en: {
    title: "Manage subscription",
    description:
      "Cancellation and downgrades take effect at the end of the paid period. Access remains available until then unless a refund, dispute, or chargeback revokes it.",
    cancel: "Cancel renewal",
    cancelling: "Scheduling cancellation…",
    resume: "Resume renewal",
    resuming: "Resuming…",
    downgrade: "Schedule downgrade",
    downgrading: "Scheduling downgrade…",
    confirmCancel:
      "Cancel automatic renewal? Your paid access will remain available until the current paid period ends.",
    confirmDowngrade:
      "Schedule this downgrade for the end of the current paid period?",
    periodEnds: "Current paid period ends",
    pendingPlan: "Scheduled plan",
    graceTitle: "Payment needs attention",
    graceDescription:
      "A renewal payment failed. Paid access remains available during the grace period. Update the payment method with your billing provider before",
    failureCount: "Failed payment events",
    suspendedTitle: "Paid access suspended",
    suspendedDescription:
      "A refund, dispute, or chargeback has suspended paid access. Cancellation remains available to stop future billing. Contact support after the provider case is resolved.",
  },
  fr: {
    title: "Gérer l’abonnement",
    description:
      "L’annulation et les changements vers une offre inférieure prennent effet à la fin de la période payée. L’accès reste disponible jusque-là, sauf révocation liée à un remboursement ou un litige.",
    cancel: "Annuler le renouvellement",
    cancelling: "Planification de l’annulation…",
    resume: "Reprendre le renouvellement",
    resuming: "Reprise…",
    downgrade: "Planifier la rétrogradation",
    downgrading: "Planification…",
    confirmCancel:
      "Annuler le renouvellement automatique ? Votre accès payant restera disponible jusqu’à la fin de la période payée.",
    confirmDowngrade:
      "Planifier cette rétrogradation pour la fin de la période payée ?",
    periodEnds: "Fin de la période payée",
    pendingPlan: "Offre planifiée",
    graceTitle: "Paiement à vérifier",
    graceDescription:
      "Un paiement de renouvellement a échoué. L’accès payant reste disponible pendant le délai de grâce. Mettez à jour le moyen de paiement auprès de votre fournisseur avant le",
    failureCount: "Échecs de paiement",
    suspendedTitle: "Accès payant suspendu",
    suspendedDescription:
      "Un remboursement, un litige ou une rétrofacturation a suspendu l’accès payant. L’annulation reste disponible pour arrêter les futurs prélèvements. Contactez le support après la résolution du dossier fournisseur.",
  },
};

const AFRICAN_COUNTRY_CODES = new Set([
  "DZ",
  "AO",
  "BJ",
  "BW",
  "BF",
  "BI",
  "CV",
  "CM",
  "CF",
  "TD",
  "KM",
  "CG",
  "CD",
  "CI",
  "DJ",
  "EG",
  "GQ",
  "ER",
  "SZ",
  "ET",
  "GA",
  "GM",
  "GH",
  "GN",
  "GW",
  "KE",
  "LS",
  "LR",
  "LY",
  "MG",
  "MW",
  "ML",
  "MR",
  "MU",
  "MA",
  "MZ",
  "NA",
  "NE",
  "NG",
  "RW",
  "ST",
  "SN",
  "SC",
  "SL",
  "SO",
  "ZA",
  "SS",
  "SD",
  "TZ",
  "TG",
  "TN",
  "UG",
  "ZM",
  "ZW",
]);

const STRIPE_COUNTRY_CODES = new Set([
  "US",
  "CA",
  "GB",
  "IE",
  "FR",
  "DE",
  "ES",
  "IT",
  "NL",
  "BE",
  "PT",
  "AT",
  "CH",
  "SE",
  "NO",
  "DK",
  "FI",
  "PL",
  "CZ",
  "GR",
  "RO",
  "BG",
  "HR",
  "HU",
  "LU",
  "LT",
  "LV",
  "EE",
  "SK",
  "SI",
  "CY",
  "MT",
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
    checkoutWith: "Checkout with {provider}",
    unavailableForPlan:
      "This payment provider is not configured for this plan yet.",
    paystack: {
      name: "Paystack",
      summary:
        "Nigeria / Africa cards, bank transfer, USSD, and local payment rails.",
      region_label: "Nigeria / Africa",
    },
    stripe: {
      name: "Stripe",
      summary: "US / Europe cards and international card checkout.",
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
    checkoutWith: "Paiement avec {provider}",
    unavailableForPlan:
      "Ce fournisseur de paiement n’est pas encore configuré pour ce forfait.",
    paystack: {
      name: "Paystack",
      summary:
        "Cartes Nigeria / Afrique, virement bancaire, USSD et moyens locaux.",
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
    checkoutFinalizing:
      "Payment received. Your subscription will update after the provider webhook is verified.",
    checkoutCancelled:
      "Checkout was cancelled. No changes were made to your plan.",
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
        price_label: "Personal plan",
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
        price_label: "Business plan",
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
        summary:
          "Custom usage, support, and deployment options for larger teams.",
        price_label: "Enterprise plan",
        billing_period: "Annual",
        account_count_label: "Custom accounts",
        features: ["Custom limits", "Advanced support", "Enterprise controls"],
      },
    },
  },
  fr: {
    apiMissing:
      "La route API de facturation n’est pas encore connectée. Affichage temporaire d’un aperçu local des forfaits.",
    checkoutComingSoon: "Le paiement n’est pas encore connecté.",
    currentPlanReason: "Ceci est votre forfait actuel.",
    checkoutFinalizing:
      "Paiement reçu. Votre abonnement sera mis à jour après vérification du webhook du fournisseur.",
    checkoutCancelled:
      "Le paiement a été annulé. Aucun changement n’a été apporté à votre forfait.",
    plans: {
      free: {
        name: "Gratuit",
        summary:
          "Commencez avec les outils ReDOCX essentiels et une utilisation mensuelle limitée.",
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
        summary:
          "Des limites plus élevées pour les flux de documents individuels.",
        price_label: "Forfait Personnel",
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
        price_label: "Forfait Business",
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
        summary:
          "Options personnalisées d’utilisation, de support et de déploiement.",
        price_label: "Forfait Enterprise",
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

function normalizePlanKey(value) {
  const normalized = String(value || "").toLowerCase();
  return PLAN_ORDER.includes(normalized) ? normalized : "free";
}

function normalizeProviderKey(value) {
  const normalized = String(value || "").toLowerCase();
  return CHECKOUT_PROVIDER_ORDER.includes(normalized) ? normalized : "stripe";
}

function providerPageCopy(language, t) {
  return {
    ...(PROVIDER_FALLBACK_COPY[language] || PROVIDER_FALLBACK_COPY.en),
    ...(t.paymentProviders || {}),
  };
}

function providerFallback(language, providerKey) {
  const copy = PROVIDER_FALLBACK_COPY[language] || PROVIDER_FALLBACK_COPY.en;
  return copy[providerKey] || PROVIDER_FALLBACK_COPY.en[providerKey] || {};
}

function defaultProviderOptions(language, recommendedProvider = "stripe") {
  return CHECKOUT_PROVIDER_ORDER.map((key) => {
    const fallback = providerFallback(language, key);
    return {
      key,
      name: fallback.name || key,
      summary: fallback.summary || "",
      region_label: fallback.region_label || "",
      configured: false,
      recommended: key === recommendedProvider,
    };
  });
}

function buildFallbackBillingState({
  language,
  entitlement,
  recommendedProvider = "stripe",
}) {
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
    provider: recommendedProvider,
    recommended_provider: recommendedProvider,
    providers: defaultProviderOptions(language, recommendedProvider),
    plans,
  };
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
  if (typeof window === "undefined") return "stripe";

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

  return "stripe";
}

function configuredProviderKeys(billingState) {
  return new Set(
    (billingState?.providers || [])
      .filter((provider) => provider?.configured)
      .map((provider) => normalizeProviderKey(provider.key)),
  );
}

function chooseInitialProvider({ billingState, user, language }) {
  const configured = configuredProviderKeys(billingState);
  const backendRecommended = normalizeProviderKey(
    billingState?.recommended_provider || billingState?.provider,
  );
  const clientRecommended = detectClientRecommendedProvider({ user, language });

  for (const candidate of [
    clientRecommended,
    backendRecommended,
    "stripe",
    "paystack",
  ]) {
    const normalized = normalizeProviderKey(candidate);
    if (!configured.size || configured.has(normalized)) return normalized;
  }

  return backendRecommended;
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

function providerConfiguredForPlan(plan, providerKey) {
  if (!plan?.can_upgrade) return false;
  const map = plan.provider_checkout_configured || {};
  if (Object.prototype.hasOwnProperty.call(map, providerKey)) {
    return Boolean(map[providerKey]);
  }
  return Boolean(plan.checkout_configured);
}

function PlanCard({
  plan,
  t,
  providerCopy,
  selectedProvider,
  busyPlan,
  onUpgrade,
  onDowngrade,
  managementCopy,
}) {
  const Icon = PLAN_ICON_MAP[plan.key] || CreditCard;
  const isBusy = busyPlan === plan.key;
  const providerKey = normalizeProviderKey(selectedProvider);
  const providerFallbackCopy = providerFallback("en", providerKey);
  const providerName =
    providerCopy[providerKey]?.name || providerFallbackCopy.name || providerKey;
  const canCheckoutWithSelectedProvider = providerConfiguredForPlan(
    plan,
    providerKey,
  );
  const buttonLabel =
    providerCopy.checkoutWith?.replace("{provider}", providerName) || t.upgrade;

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
          {plan.can_upgrade && !canCheckoutWithSelectedProvider
            ? providerCopy.unavailableForPlan
            : plan.reason}
        </p>

        {plan.can_upgrade ? (
          <button
            type="button"
            onClick={() => onUpgrade(plan.key)}
            disabled={Boolean(busyPlan) || !canCheckoutWithSelectedProvider}
            className="mt-4 inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isBusy ? t.creatingUpgrade : buttonLabel}
          </button>
        ) : plan.can_downgrade ? (
          <button
            type="button"
            onClick={() => onDowngrade(plan.key)}
            disabled={Boolean(busyPlan)}
            className="mt-4 inline-flex items-center justify-center gap-2 rounded-2xl border border-[var(--app-border)] px-5 py-3 text-sm font-semibold app-text transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isBusy ? managementCopy.downgrading : managementCopy.downgrade}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function ProviderSelector({
  providers,
  selectedProvider,
  onSelect,
  providerCopy,
  language,
}) {
  return (
    <section className="mt-6 rounded-3xl border app-surface-strong p-5 shadow-sm md:p-6">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold app-text">
            {providerCopy.title}
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 app-text-muted">
            {providerCopy.description}
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {providers.map((provider) => {
          const key = normalizeProviderKey(provider.key);
          const fallback = providerFallback(language, key);
          const selected = selectedProvider === key;
          const name = provider.name || fallback.name || key;
          const summary = provider.summary || fallback.summary || "";
          const regionLabel =
            provider.region_label || fallback.region_label || "";

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
                {provider.configured
                  ? providerCopy.configured
                  : providerCopy.notConfigured}
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
  const managementCopy = useMemo(
    () =>
      SUBSCRIPTION_MANAGEMENT_COPY[language] ||
      SUBSCRIPTION_MANAGEMENT_COPY.en,
    [language],
  );

  const [billingState, setBillingState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyPlan, setBusyPlan] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("");
  const [organizationName, setOrganizationName] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") return;

    const params = new URLSearchParams(window.location.search);
    const checkout = String(params.get("checkout") || "").toLowerCase();
    const provider = params.get("provider");

    if (provider) {
      setSelectedProvider(normalizeProviderKey(provider));
    }

    if (checkout === "success") {
      setMessage(
        t.checkoutFinalizing ||
          FALLBACK_PLAN_COPY[language]?.checkoutFinalizing ||
          FALLBACK_PLAN_COPY.en.checkoutFinalizing,
      );
      void reloadAccount?.();
    } else if (
      checkout === "cancelled" ||
      checkout === "canceled" ||
      checkout === "cancel"
    ) {
      setMessage(
        t.checkoutCancelled ||
          FALLBACK_PLAN_COPY[language]?.checkoutCancelled ||
          FALLBACK_PLAN_COPY.en.checkoutCancelled,
      );
    }
  }, [language, reloadAccount, t.checkoutCancelled, t.checkoutFinalizing]);

  useEffect(() => {
    let cancelled = false;

    async function loadBilling() {
      if (!authChecked) return;

      if (!user) {
        const recommendedProvider = detectClientRecommendedProvider({
          user,
          language,
        });
        setLoading(false);
        setBillingState(null);
        setSelectedProvider((current) => current || recommendedProvider);
        return;
      }

      setLoading(true);
      setError("");

      try {
        const data = await getBillingPlans();
        if (cancelled) return;

        setBillingState(data);
        setOrganizationName(
          (current) =>
            current || String(data?.entitlement?.organization_name || ""),
        );
        setSelectedProvider(
          (current) =>
            current ||
            chooseInitialProvider({ billingState: data, user, language }),
        );
      } catch (caught) {
        if (cancelled) return;

        if (isNotFoundError(caught)) {
          const recommendedProvider = detectClientRecommendedProvider({
            user,
            language,
          });
          const fallbackState = buildFallbackBillingState({
            language,
            entitlement,
            recommendedProvider,
          });
          setBillingState(fallbackState);
          setSelectedProvider(
            (current) =>
              current ||
              chooseInitialProvider({
                billingState: fallbackState,
                user,
                language,
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
  }, [
    authChecked,
    user,
    entitlement,
    language,
    t.loadFailed,
    t.billingApiMissing,
  ]);

  async function handleUpgrade(targetPlan) {
    const provider = normalizeProviderKey(selectedProvider);
    const isOrganizationPlan = ["business", "enterprise"].includes(targetPlan);
    const existingOrganizationId = billingState?.entitlement?.organization_id;
    const normalizedOrganizationName = organizationName
      .trim()
      .replace(/\s+/g, " ");

    if (isOrganizationPlan && !existingOrganizationId) {
      if (!normalizedOrganizationName) {
        setError(t.organizationNameRequired);
        return;
      }
      if (normalizedOrganizationName.length < 2) {
        setError(t.organizationNameTooShort);
        return;
      }
      if (normalizedOrganizationName.length > 100) {
        setError(t.organizationNameTooLong);
        return;
      }
    }

    setBusyPlan(targetPlan);
    setError("");
    setMessage("");

    try {
      const data = await createBillingUpgradeIntent(targetPlan, {
        provider,
        regionHint: getClientRegionHint({ user, language }),
        organizationName: isOrganizationPlan
          ? normalizedOrganizationName
          : undefined,
      });

      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      setMessage(data?.message || t.checkoutNotConfigured);
      await reloadAccount?.();
      const latest = await getBillingPlans();
      setBillingState(latest);
      setSelectedProvider(
        (current) =>
          current ||
          chooseInitialProvider({ billingState: latest, user, language }),
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


  async function handleSubscriptionAction(action, targetPlan = null) {
    const confirmation =
      action === "cancel"
        ? managementCopy.confirmCancel
        : action === "downgrade"
          ? managementCopy.confirmDowngrade
          : "";

    if (confirmation && typeof window !== "undefined" && !window.confirm(confirmation)) {
      return;
    }

    const busyKey = targetPlan || `manage:${action}`;
    setBusyPlan(busyKey);
    setError("");
    setMessage("");

    try {
      const data = await manageBillingSubscription(action, { targetPlan });
      setMessage(data?.message || "Subscription updated.");
      await reloadAccount?.();
      const latest = await getBillingPlans();
      setBillingState(latest);
    } catch (caught) {
      setError(getErrorMessage(caught, "Could not update the subscription."));
    } finally {
      setBusyPlan("");
    }
  }

  const providers = billingState?.providers?.length
    ? billingState.providers
    : defaultProviderOptions(language, selectedProvider || "stripe");

  const selectedProviderKey = normalizeProviderKey(
    selectedProvider || billingState?.recommended_provider,
  );
  const existingOrganizationId = billingState?.entitlement?.organization_id;
  const showsOrganizationUpgrade = (billingState?.plans || []).some(
    (plan) =>
      ["business", "enterprise"].includes(plan?.key) && plan?.can_upgrade,
  );

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 app-hero-overlay" />

        <div className="relative mx-auto max-w-7xl px-6 py-8 md:px-8 md:py-10">
          <button
            type="button"
            onClick={() => router.push("/")}
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
              <h2 className="text-lg font-semibold app-text">
                {t.signInTitle}
              </h2>
              <p className="mt-2 text-sm app-text-muted">
                {t.signInDescription}
              </p>
              <a
                href={buildAuthLoginUrl({ language, returnTo: "/billing" })}
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

              {billingState?.management?.access_revoked_at ? (
                <div className="mt-6 flex items-start gap-3 rounded-3xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                  <div>
                    <p className="font-semibold">
                      {managementCopy.suspendedTitle}
                    </p>
                    <p className="mt-1 leading-6">
                      {managementCopy.suspendedDescription}
                    </p>
                  </div>
                </div>
              ) : billingState?.management?.grace_period_end ? (
                <div className="mt-6 flex items-start gap-3 rounded-3xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-100">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                  <div>
                    <p className="font-semibold">{managementCopy.graceTitle}</p>
                    <p className="mt-1 leading-6">
                      {managementCopy.graceDescription}{" "}
                      {new Date(
                        billingState.management.grace_period_end,
                      ).toLocaleDateString(language)}.
                    </p>
                    {billingState.management.payment_failure_count ? (
                      <p className="mt-1 text-xs">
                        {managementCopy.failureCount}: {billingState.management.payment_failure_count}
                      </p>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {showsOrganizationUpgrade && !existingOrganizationId ? (
                <section className="mt-6 rounded-3xl border app-surface-strong p-5 md:p-6">
                  <label
                    htmlFor="organization-name"
                    className="text-sm font-semibold app-text"
                  >
                    {t.organizationName}
                  </label>
                  <p className="mt-1 text-sm app-text-muted">
                    {t.organizationNameHelp}
                  </p>
                  <input
                    id="organization-name"
                    type="text"
                    value={organizationName}
                    onChange={(event) =>
                      setOrganizationName(event.target.value)
                    }
                    maxLength={100}
                    autoComplete="organization"
                    placeholder={t.organizationNamePlaceholder}
                    className="mt-4 w-full rounded-2xl border px-4 py-3 text-sm app-text outline-none transition focus:border-[var(--app-text-soft)] md:max-w-xl"
                  />
                </section>
              ) : null}

              {billingState?.management?.can_cancel ||
              billingState?.management?.can_resume ? (
                <section className="mt-6 rounded-3xl border app-surface-strong p-5 shadow-sm md:p-6">
                  <h2 className="text-lg font-semibold app-text">
                    {managementCopy.title}
                  </h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 app-text-muted">
                    {managementCopy.description}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-3 text-xs app-text-soft">
                    {billingState.management.current_period_end ? (
                      <span>
                        {managementCopy.periodEnds}: {new Date(
                          billingState.management.current_period_end,
                        ).toLocaleDateString(language)}
                      </span>
                    ) : null}
                    {billingState.management.pending_plan ? (
                      <span>
                        {managementCopy.pendingPlan}: {billingState.management.pending_plan}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-5 flex flex-wrap gap-3">
                    {billingState.management.can_resume ? (
                      <button
                        type="button"
                        onClick={() => handleSubscriptionAction("resume")}
                        disabled={Boolean(busyPlan)}
                        className="inline-flex items-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busyPlan === "manage:resume" ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : null}
                        {busyPlan === "manage:resume"
                          ? managementCopy.resuming
                          : managementCopy.resume}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleSubscriptionAction("cancel")}
                        disabled={Boolean(busyPlan)}
                        className="inline-flex items-center gap-2 rounded-2xl border border-red-400/30 bg-red-400/10 px-5 py-3 text-sm font-semibold text-red-200 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {busyPlan === "manage:cancel" ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : null}
                        {busyPlan === "manage:cancel"
                          ? managementCopy.cancelling
                          : managementCopy.cancel}
                      </button>
                    )}
                  </div>
                </section>
              ) : null}

              <ProviderSelector
                providers={providers}
                selectedProvider={selectedProviderKey}
                onSelect={setSelectedProvider}
                providerCopy={providerCopy}
                language={language}
              />

              <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                {(billingState?.plans || []).map((plan) => (
                  <PlanCard
                    key={plan.key}
                    plan={plan}
                    t={t}
                    providerCopy={providerCopy}
                    selectedProvider={selectedProviderKey}
                    busyPlan={busyPlan}
                    onUpgrade={handleUpgrade}
                    onDowngrade={(targetPlan) =>
                      handleSubscriptionAction("downgrade", targetPlan)
                    }
                    managementCopy={managementCopy}
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
