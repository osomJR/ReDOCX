"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
  confirmPaystackCheckout,
  createBillingUpgradeIntent,
  getBillingPlans,
  manageBillingSubscription,
} from "@/lib/api_client";
import { buildAuthLoginUrl } from "@/lib/auth_urls";
import {
  billingFallbackPlanTranslations,
  billingPageTranslations,
  billingProviderFallbackTranslations,
  billingSeatPricingTranslations,
  billingSubscriptionManagementTranslations,
  getPageRuntimeCopy,
  resolveErrorMessage,
} from "@/lib/translations";

const PLAN_ICON_MAP = {
  free: Sparkles,
  personal: CreditCard,
  business: UsersRound,
  enterprise: ShieldCheck,
};

const PLAN_ORDER = ["free", "personal", "business", "enterprise"];
const CHECKOUT_PROVIDER_ORDER = ["paystack", "stripe"];
const PAYSTACK_CONFIRMATION_DELAYS_MS = [0, 1_000, 2_000, 4_000, 7_000];

const BUSINESS_MAX_SEATS = 19;
const SEAT_PRICING_COPY = billingSeatPricingTranslations;

function normalizeUnitAmountKobo(value) {
  if (
    value === null ||
    value === undefined ||
    value === "" ||
    typeof value === "boolean"
  ) {
    return null;
  }

  const amount = Number(value);
  return Number.isSafeInteger(amount) && amount >= 0 ? amount : null;
}

function formatNairaFromKobo(amountKobo) {
  const normalizedAmountKobo = normalizeUnitAmountKobo(amountKobo);
  if (normalizedAmountKobo === null) return "—";

  return new Intl.NumberFormat("en-NG", {
    style: "currency",
    currency: "NGN",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(normalizedAmountKobo / 100);
}

function resolveSeatSelection(value, planKey) {
  const raw = String(value ?? "").trim();
  if (!/^\d+$/.test(raw)) {
    return { valid: false, seats: null, error: "invalid" };
  }

  const seats = Number(raw);
  if (!Number.isSafeInteger(seats) || seats < 1) {
    return { valid: false, seats: null, error: "invalid" };
  }
  if (planKey === "business" && seats > BUSINESS_MAX_SEATS) {
    return { valid: false, seats, error: "business_limit" };
  }
  return { valid: true, seats, error: "" };
}

function waitFor(milliseconds, signal) {
  if (!milliseconds) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeoutId);
      reject(new DOMException("Request aborted", "AbortError"));
    };
    const timeoutId = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);

    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
const SUBSCRIPTION_MANAGEMENT_COPY = billingSubscriptionManagementTranslations;

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
const PROVIDER_FALLBACK_COPY = billingProviderFallbackTranslations;
const FALLBACK_PLAN_COPY = billingFallbackPlanTranslations;

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
      unit_amount_kobo: null,
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

function getErrorMessage(error, language, fallbackCode = "INTERNAL_ERROR") {
  return resolveErrorMessage(error, language, fallbackCode);
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
  periodLocked = false,
  language,
  initialSeatCount = 1,
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
  const seatCopy = SEAT_PRICING_COPY[language] || SEAT_PRICING_COPY.en;
  const isPaidPlan = ["personal", "business", "enterprise"].includes(plan.key);
  const isSeatPricedPlan = ["business", "enterprise"].includes(plan.key);
  const unitAmountKobo = normalizeUnitAmountKobo(plan.unit_amount_kobo);
  const safeInitialSeatCount =
    Number.isSafeInteger(Number(initialSeatCount)) && Number(initialSeatCount) >= 1
      ? Number(initialSeatCount)
      : 1;
  const [seatInput, setSeatInput] = useState(String(safeInitialSeatCount));
  const selection = resolveSeatSelection(seatInput, plan.key);
  const seatError =
    selection.error === "business_limit"
      ? seatCopy.businessLimit
      : selection.error
        ? seatCopy.invalidSeats
        : "";
  const recurringTotalKobo =
    selection.valid &&
    unitAmountKobo !== null &&
    Number.isSafeInteger(selection.seats * unitAmountKobo)
      ? selection.seats * unitAmountKobo
      : null;
  const displayPriceLabel =
    plan.key === "personal" && unitAmountKobo !== null
      ? formatNairaFromKobo(unitAmountKobo)
      : isSeatPricedPlan && unitAmountKobo !== null
        ? `${formatNairaFromKobo(unitAmountKobo)} / ${seatCopy.perSeat}`
        : plan.price_label;
  const canSubmitUpgrade =
    plan.can_upgrade &&
    canCheckoutWithSelectedProvider &&
    (!isPaidPlan || unitAmountKobo !== null) &&
    (!isSeatPricedPlan || (selection.valid && recurringTotalKobo !== null));

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
          <p className="text-2xl font-semibold app-text">{displayPriceLabel}</p>
          <p className="mt-1 text-xs app-text-soft">
            {plan.billing_period} · {plan.account_count_label}
          </p>
        </div>

        {isSeatPricedPlan && plan.can_upgrade ? (
          <div className="mt-4 rounded-2xl border border-[var(--app-border)] app-surface p-4">
            <label
              htmlFor={`seat-count-${plan.key}`}
              className="block text-sm font-semibold app-text"
            >
              {seatCopy.seatsLabel}
            </label>
            <input
              id={`seat-count-${plan.key}`}
              type="number"
              inputMode="numeric"
              min={1}
              max={plan.key === "business" ? BUSINESS_MAX_SEATS : undefined}
              step={1}
              value={seatInput}
              onChange={(event) => setSeatInput(event.target.value)}
              aria-invalid={Boolean(seatError)}
              aria-describedby={`seat-count-${plan.key}-help`}
              className="mt-2 w-full rounded-xl border border-[var(--app-border)] bg-transparent px-3 py-2.5 text-base font-semibold app-text outline-none transition focus:border-[var(--app-border-strong)]"
            />
            <p
              id={`seat-count-${plan.key}-help`}
              className={`mt-2 text-xs leading-5 ${
                seatError ? "text-red-300" : "app-text-soft"
              }`}
            >
              {seatError ||
                (plan.key === "business"
                  ? seatCopy.businessHelp
                  : seatCopy.enterpriseHelp)}
            </p>

            <div className="mt-3 flex items-end justify-between gap-4 border-t border-[var(--app-border)] pt-3">
              <span className="text-xs font-semibold uppercase tracking-[0.1em] app-text-soft">
                {seatCopy.total}
              </span>
              <span className="text-xl font-semibold app-text">
                {recurringTotalKobo === null
                  ? "—"
                  : formatNairaFromKobo(recurringTotalKobo)}
              </span>
            </div>
          </div>
        ) : null}

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
          {periodLocked && !plan.is_current
            ? managementCopy.periodLocked
            : plan.can_upgrade && !canCheckoutWithSelectedProvider
              ? providerCopy.unavailableForPlan
              : plan.reason}
        </p>

        {plan.can_upgrade ? (
          <button
            type="button"
            onClick={() =>
              onUpgrade(
                plan.key,
                isSeatPricedPlan && selection.valid ? selection.seats : undefined,
              )
            }
            disabled={Boolean(busyPlan) || !canSubmitUpgrade}
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
  const checkoutConfirmationRef = useRef("");

  useEffect(() => {
    if (typeof window === "undefined" || !authChecked) return undefined;

    const params = new URLSearchParams(window.location.search);
    const checkout = String(params.get("checkout") || "").toLowerCase();
    const reference = String(
      params.get("reference") || params.get("trxref") || "",
    ).trim();
    const provider = normalizeProviderKey(
      params.get("provider") || (reference ? "paystack" : ""),
    );

    if (provider) {
      setSelectedProvider(provider);
    }

    const isSuccessfulReturn =
      checkout === "success" || (provider === "paystack" && Boolean(reference));

    if (isSuccessfulReturn && provider === "paystack" && reference) {
      const confirmationKey = `${provider}:${reference}`;
      if (checkoutConfirmationRef.current === confirmationKey) {
        return undefined;
      }

      checkoutConfirmationRef.current = confirmationKey;
      const controller = new AbortController();
      let completed = false;

      setMessage(
        t.checkoutFinalizing ||
          FALLBACK_PLAN_COPY[language]?.checkoutFinalizing ||
          FALLBACK_PLAN_COPY.en.checkoutFinalizing,
      );
      setError("");

      async function confirmPayment() {
        let lastError = null;

        for (const delayMs of PAYSTACK_CONFIRMATION_DELAYS_MS) {
          try {
            await waitFor(delayMs, controller.signal);
            const data = await confirmPaystackCheckout(reference, {
              signal: controller.signal,
            });
            if (controller.signal.aborted) return;

            if (data?.billing_state) {
              setBillingState(data.billing_state);
              setOrganizationName((current) =>
                current ||
                String(data.billing_state?.entitlement?.organization_name || ""),
              );
            }
            setMessage(getPageRuntimeCopy("billing", language).paymentVerified);
            setError("");
            completed = true;

            params.delete("reference");
            params.delete("trxref");
            params.set("checkout", "success");
            params.set("provider", "paystack");
            const query = params.toString();
            window.history.replaceState(
              window.history.state,
              "",
              `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
            );

            await reloadAccount?.({
              background: true,
              forceRefresh: true,
              allowCurrentAccountFallback: true,
            });
            return;
          } catch (caught) {
            if (caught?.name === "AbortError" || controller.signal.aborted) {
              return;
            }
            lastError = caught;

            const retryable =
              caught?.rawCode === "paystack_payment_not_confirmed" || caught?.code === "paystack_payment_not_confirmed" ||
              Number(caught?.status || 0) >= 500;
            if (!retryable) break;
          }
        }

        if (controller.signal.aborted) return;

        const retryableFailure =
          lastError?.rawCode === "paystack_payment_not_confirmed" || lastError?.code === "paystack_payment_not_confirmed" ||
          Number(lastError?.status || 0) >= 500;
        if (retryableFailure) {
          setMessage(
            t.checkoutConfirmationDelayed ||
              FALLBACK_PLAN_COPY[language]?.checkoutConfirmationDelayed ||
              FALLBACK_PLAN_COPY.en.checkoutConfirmationDelayed,
          );
        } else {
          setError(getErrorMessage(lastError, language, "SUBSCRIPTION_OPERATION_FAILED"));
        }
      }

      void confirmPayment();

      return () => {
        controller.abort();
        if (!completed && checkoutConfirmationRef.current === confirmationKey) {
          checkoutConfirmationRef.current = "";
        }
      };
    }

    if (isSuccessfulReturn) {
      setMessage(
        t.checkoutFinalizing ||
          FALLBACK_PLAN_COPY[language]?.checkoutFinalizing ||
          FALLBACK_PLAN_COPY.en.checkoutFinalizing,
      );
      void reloadAccount?.({
        background: true,
        forceRefresh: true,
        allowCurrentAccountFallback: true,
      });
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
    return undefined;
  }, [
    authChecked,
    language,
    reloadAccount,
    t.checkoutCancelled,
    t.checkoutConfirmationDelayed,
    t.checkoutFinalizing,
    t.upgradeFailed,
  ]);

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

        setError(getErrorMessage(caught, language, "BILLING_UNAVAILABLE"));
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

  async function handleUpgrade(targetPlan, selectedSeatCount) {
    const provider = normalizeProviderKey(selectedProvider);
    const isOrganizationPlan = ["business", "enterprise"].includes(targetPlan);
    const seatSelection = isOrganizationPlan
      ? resolveSeatSelection(selectedSeatCount, targetPlan)
      : { valid: true, seats: undefined, error: "" };
    if (!seatSelection.valid) {
      const seatCopy = SEAT_PRICING_COPY[language] || SEAT_PRICING_COPY.en;
      setError(
        seatSelection.error === "business_limit"
          ? seatCopy.businessLimit
          : seatCopy.invalidSeats,
      );
      return;
    }
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
        seatCount: isOrganizationPlan ? seatSelection.seats : undefined,
      });

      if (data?.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      setMessage(t.checkoutNotConfigured);
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
        setError(getErrorMessage(caught, language, "SUBSCRIPTION_OPERATION_FAILED"));
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
      setMessage(getPageRuntimeCopy("billing", language).subscriptionUpdated);
      await reloadAccount?.();
      const latest = await getBillingPlans();
      setBillingState(latest);
    } catch (caught) {
      setError(getErrorMessage(caught, language, "SUBSCRIPTION_OPERATION_FAILED"));
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
                  {billingState.management.period_locked &&
                  billingState.management.plan_change_available_at ? (
                    <p className="mt-2 text-sm font-semibold app-text">
                      {managementCopy.planChangesAvailable} {new Date(
                        billingState.management.plan_change_available_at,
                      ).toLocaleDateString(language)}.
                    </p>
                  ) : null}
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
                    periodLocked={Boolean(billingState?.management?.period_locked)}
                    language={language}
                    initialSeatCount={billingState?.entitlement?.account_count || 1}
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
