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
} from "@/lib/api_client";
import { billingPageTranslations } from "@/lib/translations";

const PLAN_ICON_MAP = {
  free: Sparkles,
  personal: CreditCard,
  business: UsersRound,
  enterprise: ShieldCheck,
};

const PLAN_ORDER = ["free", "personal", "business", "enterprise"];

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
      reason: isCurrent ? copy.currentPlanReason : copy.checkoutComingSoon,
    };
  });

  return {
    current_plan: currentPlanKey,
    current_plan_name:
      plans.find((plan) => plan.is_current)?.name || copy.plans.free.name,
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

function PlanCard({ plan, t, busyPlan, onUpgrade }) {
  const Icon = PLAN_ICON_MAP[plan.key] || CreditCard;
  const isBusy = busyPlan === plan.key;

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
          {plan.reason}
        </p>

        {plan.can_upgrade ? (
          <button
            type="button"
            onClick={() => onUpgrade(plan.key)}
            disabled={Boolean(busyPlan)}
            className="mt-4 inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isBusy ? t.creatingUpgrade : t.upgrade}
          </button>
        ) : null}
      </div>
    </article>
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

  const [billingState, setBillingState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyPlan, setBusyPlan] = useState("");

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
      } catch (caught) {
        if (cancelled) return;

        if (isNotFoundError(caught)) {
          setBillingState(buildFallbackBillingState({ language, entitlement }));
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
    setBusyPlan(targetPlan);
    setError("");
    setMessage("");

    try {
      const data = await createBillingUpgradeIntent(targetPlan);

      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      setMessage(data?.message || t.checkoutNotConfigured);
      await reloadAccount?.();
      const latest = await getBillingPlans();
      setBillingState(latest);
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

              <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                {(billingState?.plans || []).map((plan) => (
                  <PlanCard
                    key={plan.key}
                    plan={plan}
                    t={t}
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
