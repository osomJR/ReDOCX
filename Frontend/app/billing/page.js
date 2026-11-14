"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
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
import BillingSeats from "@/components/billing_seats";
import { AccountSessionRetry } from "@/components/auth_controls";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  confirmPaystackCheckout,
  createBillingUpgradeIntent,
  getBillingPlans,
  manageBillingSubscription,
} from "@/lib/api_client";
import { buildAuthLoginUrl, getBillingReturnPath } from "@/lib/auth_urls";
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
const CHECKOUT_PROVIDER_ORDER = ["paystack"];
const PAYSTACK_CONFIRMATION_DELAYS_MS = [0, 1_000, 2_000, 4_000, 7_000];
const BILLING_LOAD_DELAYS_MS = [250, 750, 1_500];
const CHECKOUT_NEEDS_VERIFICATION = new Set([
  "checking", "pending", "failed", "abandoned", "reversed", "review", "payment_pending",
  "sign_in_again", "account_deactivation_pending",
]);
const CHECKOUT_CHECK_UNAVAILABLE = new Set(["checking", "sign_in_again", "account_deactivation_pending"]);

const PAYSTACK_RETURN_COPY = {
  en: {
    checking: "Checking the transaction with Paystack…",
    verified: "Payment verified. Your purchase has been applied.",
    pending: "Paystack has yet to confirm the outcome. Check this payment before starting another.",
    failed: "Paystack reports that the payment failed.",
    abandoned: "This checkout was left incomplete.",
    reversed: "Paystack reports that the payment was reversed.",
    payment_refunded: "Paystack confirmed the refund for this purchase.",
    account_deactivation_pending: "Your account is deactivated. ReDOCX is checking the status of any outstanding payments.",
    cancelled: "You left checkout. Check any existing payment below.",
    missing_reference: "This return link has no payment reference. Check the existing checkout below.",
    sign_in_again: "Sign in again to check your payment.",
    review: "We couldn't confirm this payment. Check its status or contact support before paying again.",
    check: "Check payment",
    resume: "Continue existing checkout",
    open: "You have a Paystack checkout in progress.",
    openForPlan: "Your Paystack checkout for {plan} is still in progress.",
    payment_pending: "Paystack reports a payment. We're verifying your subscription. Check this payment before paying again.",
    retry_ready: "Your previous checkout has closed. You can now start a new checkout from the plan below.",
    retryAt: "The next check is scheduled for {time}. We'll verify the previous checkout before offering a new one.",
    retryUnknown: "Check the payment again shortly. If it remains unresolved, contact support before paying again.",
    retryPlans: "Retry loading billing",
    plansUnavailable: "Billing status unavailable",
    startNew: "Start a new checkout",
    checkoutUnavailable: "Couldn't start Paystack checkout. Please try again in a moment.",
    personal: "Your Personal subscription",
    scheduled: "Renewal has stopped. Start checkout for the new plan after {date}.",
    scheduledWithoutDate: "Renewal has stopped. Start checkout for the new plan when your paid period ends.",
    renewalOff: "Renewal has stopped. Your paid access continues until {date}.",
    renewalOn: "Subscription renewal is active.",
  },
  fr: {
    checking: "Vérification de la transaction auprès de Paystack…",
    verified: "Paiement vérifié. Votre achat est appliqué à votre compte.",
    pending: "Paystack doit encore confirmer le résultat. Vérifiez ce paiement avant d’en lancer un autre.",
    failed: "Paystack indique que le paiement a échoué.",
    abandoned: "Ce paiement n’a pas été terminé.",
    reversed: "Paystack indique que le paiement a été annulé.",
    payment_refunded: "Paystack a confirmé le remboursement de cet achat.",
    account_deactivation_pending: "Votre compte est désactivé. ReDOCX vérifie les paiements en cours.",
    cancelled: "Vous avez quitté la page de paiement. Vérifiez tout paiement en cours ci-dessous.",
    missing_reference: "Ce lien ne contient aucune référence. Vérifiez le paiement existant ci-dessous.",
    sign_in_again: "Reconnectez-vous pour vérifier votre paiement.",
    review: "Impossible de confirmer ce paiement. Vérifiez son état ou contactez l’assistance avant de payer à nouveau.",
    check: "Vérifier le paiement",
    resume: "Reprendre le paiement en cours",
    open: "Un paiement Paystack est en cours.",
    openForPlan: "Votre paiement Paystack pour le forfait {plan} est toujours en cours.",
    payment_pending: "Paystack signale un paiement. Nous vérifions votre abonnement. Vérifiez ce paiement avant de payer à nouveau.",
    retry_ready: "Votre session de paiement précédente est terminée. Vous pouvez en lancer une nouvelle depuis l’offre ci-dessous.",
    retryAt: "La prochaine vérification est prévue à {time}. Nous vérifierons le paiement précédent avant d’en proposer un nouveau.",
    retryUnknown: "Vérifiez à nouveau le paiement dans quelques instants. Si son état reste incertain, contactez l’assistance avant de payer à nouveau.",
    retryPlans: "Réessayer le chargement de la facturation",
    plansUnavailable: "État de la facturation indisponible",
    startNew: "Lancer un nouveau paiement",
    checkoutUnavailable: "Impossible de lancer le paiement Paystack. Réessayez dans quelques instants.",
    personal: "Votre abonnement Personnel",
    scheduled: "Le renouvellement est arrêté. Souscrivez au nouveau forfait après le {date}.",
    scheduledWithoutDate: "Le renouvellement est arrêté. Souscrivez au nouveau forfait à la fin de la période payée.",
    renewalOff: "Le renouvellement est arrêté. Votre accès payant reste disponible jusqu’au {date}.",
    renewalOn: "Le renouvellement de l’abonnement est actif.",
  },
};

function planChangeMessage(data, language, copy) {
  const date = data?.effective_at || data?.current_period_end;
  const formatted = date ? new Date(date).toLocaleDateString(language) : "";
  if (data?.requires_checkout_at_period_end) {
    return formatted ? copy.scheduled.replace("{date}", formatted) : copy.scheduledWithoutDate;
  }
  if (data?.cancel_at_period_end === true) {
    return formatted ? copy.renewalOff.replace("{date}", formatted) : getPageRuntimeCopy("billing", language).subscriptionUpdated;
  }
  if (data?.action === "resume") return copy.renewalOn;
  return getPageRuntimeCopy("billing", language).subscriptionUpdated;
}

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

function isAccountOperationPending(error) {
  return (error?.rawCode || error?.code) === "account_operation_pending";
}

async function loadBillingPlans(signal) {
  for (const delay of BILLING_LOAD_DELAYS_MS) {
    try {
      return await getBillingPlans({ signal });
    } catch (error) {
      if (!isAccountOperationPending(error)) throw error;
      await waitFor(delay, signal);
    }
  }
  return getBillingPlans({ signal });
}
const SUBSCRIPTION_MANAGEMENT_COPY = billingSubscriptionManagementTranslations;

const PROVIDER_FALLBACK_COPY = billingProviderFallbackTranslations;
const FALLBACK_PLAN_COPY = billingFallbackPlanTranslations;

function normalizePlanKey(value) {
  const normalized = String(value || "").toLowerCase();
  return PLAN_ORDER.includes(normalized) ? normalized : "free";
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

function defaultProviderOptions(language, recommendedProvider = "paystack") {
  return CHECKOUT_PROVIDER_ORDER.map((key) => {
    const fallback = providerFallback(language, key);
    return {
      key,
      name: fallback.name || key,
      summary: fallback.summary || "",
      region_label: fallback.region_label || "",
      configured: null,
      recommended: key === recommendedProvider,
    };
  });
}

function buildFallbackBillingState({
  language,
  entitlement,
  recommendedProvider = "paystack",
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
  busyPlan,
  onUpgrade,
  onDowngrade,
  managementCopy,
  periodLocked = false,
  checkoutInProgress = false,
  retryLabel,
  language,
  initialSeatCount = 1,
}) {
  const Icon = PLAN_ICON_MAP[plan.key] || CreditCard;
  const isBusy = busyPlan === plan.key;
  const providerKey = "paystack";
  const providerFallbackCopy = providerFallback("en", providerKey);
  const providerName =
    providerCopy[providerKey]?.name || providerFallbackCopy.name || providerKey;
  const canCheckoutWithSelectedProvider = providerConfiguredForPlan(
    plan,
    providerKey,
  );
  const buttonLabel = retryLabel ||
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
            disabled={Boolean(busyPlan) || checkoutInProgress || !canSubmitUpgrade}
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

function PaymentProvider({
  providers,
  providerCopy,
  language,
  unavailableLabel,
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
          const key = provider.key;
          const fallback = providerFallback(language, key);
          const name = provider.name || fallback.name || key;
          const summary = fallback.summary || provider.summary || "";
          const regionLabel =
            provider.region_label || fallback.region_label || "";

          return (
            <div key={key} className="rounded-2xl border app-surface-strong p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-base font-semibold app-text">{name}</p>
                  <p className="mt-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                    {regionLabel}
                  </p>
                </div>

              </div>

              <p className="mt-3 text-sm leading-6 app-text-muted">{summary}</p>
              <p className="mt-3 text-xs app-text-soft">
                {provider.configured === null
                  ? unavailableLabel
                  : provider.configured ? providerCopy.configured : providerCopy.notConfigured}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function BillingPage() {
  const router = useRouter();
  const pathname = usePathname();
  const { language } = useLanguage();
  const { user, authChecked, entitlement, reloadAccount, error: accountError } = useAccount();
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
  const [organizationName, setOrganizationName] = useState("");
  const [checkoutReturn, setCheckoutReturn] = useState(null);
  const [checkoutStatus, setCheckoutStatus] = useState("");
  const [confirmationAttempt, setConfirmationAttempt] = useState(0);
  const [billingLoadAttempt, setBillingLoadAttempt] = useState(0);
  const [billingLoadError, setBillingLoadError] = useState("");
  const mutationInProgress = useRef(false);
  const returnMessage = PAYSTACK_RETURN_COPY[language] || PAYSTACK_RETURN_COPY.en;
  const loadedBillingUserRef = useRef(null);
  const billingRequestRef = useRef(0);
  const checkoutUpdateRef = useRef(null);
  const billingAccountKey = JSON.stringify([user?.id || null, entitlement]);

  useEffect(() => {
    const returnTo = getBillingReturnPath(window.location.href, window.location.origin) || pathname;
    const allowed = new URLSearchParams(returnTo.split("?")[1]);
    setCheckoutReturn({
      reference: allowed.get("seat_job") ? "" : allowed.get("reference") || allowed.get("trxref") || "",
      outcome: allowed.get("seat_job") ? "" : (allowed.get("checkout") || "").toLowerCase(),
      returnTo,
    });
  }, [pathname]);

  const openCheckout = billingState?.paystack_checkout;
  const checkoutReference = checkoutReturn?.reference || openCheckout?.reference || "";
  const checkoutOutcome = checkoutReturn?.outcome || "";
  const openCheckoutPlan = billingState?.plans?.find(plan => plan.key === openCheckout?.target_plan);
  const retryAtMs = Date.parse(openCheckout?.retry_at);
  const checkedAtMs = Date.parse(openCheckout?.checked_at);
  const retryScheduled = Number.isFinite(retryAtMs) && checkedAtMs < retryAtMs;
  const paystackStatus = openCheckout?.status;
  const showCheckoutLink = Boolean(
    openCheckout?.checkout_url &&
    retryScheduled && retryAtMs > Date.now() &&
    checkoutStatus === "abandoned" && checkoutReference === openCheckout.reference,
  );
  const checkoutInProgress = Boolean(openCheckout) ||
    CHECKOUT_NEEDS_VERIFICATION.has(checkoutStatus) ||
    Boolean(checkoutReturn?.reference && !checkoutStatus);
  let checkoutMessage = returnMessage[checkoutStatus];
  if (!CHECKOUT_CHECK_UNAVAILABLE.has(checkoutStatus)) {
    if (paystackStatus === "payment_pending") checkoutMessage = returnMessage.payment_pending;
    if (paystackStatus === "review") checkoutMessage = returnMessage.review;
    if (paystackStatus === "checkout_declined") checkoutMessage = returnMessage.failed;
  }
  if (!checkoutMessage) {
    checkoutMessage = openCheckoutPlan
      ? returnMessage.openForPlan.replace("{plan}", openCheckoutPlan.name)
      : returnMessage.open;
  }
  const canCheckPayment = Boolean(openCheckout?.reference || (
    checkoutReference && CHECKOUT_NEEDS_VERIFICATION.has(checkoutStatus)
  )) && !CHECKOUT_CHECK_UNAVAILABLE.has(checkoutStatus);

  function checkPayment(reference) {
    setCheckoutReturn({
      reference,
      returnTo: `${pathname}?reference=${encodeURIComponent(reference)}`,
    });
    setConfirmationAttempt(value => value + 1);
  }

  useEffect(() => {
    if (!authChecked || !user?.id || !checkoutReturn) return undefined;
    const userId = user.id;
    const reference = checkoutReference;
    if (!reference) {
      if (["cancelled", "canceled", "cancel"].includes(checkoutOutcome)) setCheckoutStatus("cancelled");
      else if (checkoutOutcome === "success") setCheckoutStatus("missing_reference");
      return undefined;
    }

    const controller = new AbortController();
    setCheckoutStatus("checking");
    async function confirmPayment() {
      for (const delay of PAYSTACK_CONFIRMATION_DELAYS_MS) {
        try {
          if (delay) await waitFor(delay, controller.signal);
          const data = await confirmPaystackCheckout(reference, { signal: controller.signal });
          if (controller.signal.aborted) return;
          if (data.confirmed) {
            if (data.billing_state) {
              billingRequestRef.current += 1;
              loadedBillingUserRef.current = userId;
              setBillingState(data.billing_state);
              setLoading(false);
              setBillingLoadError("");
            } else {
              checkoutUpdateRef.current = { userId, checkout: null };
              setBillingState(current => current ? { ...current, paystack_checkout: null } : current);
            }
            setCheckoutStatus("verified");
            const url = new URL(window.location.href);
            for (const key of ["reference", "trxref", "checkout", "provider"]) url.searchParams.delete(key);
            window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
            void reloadAccount?.({ background: true, forceRefresh: true });
            return;
          }
          const updatedCheckout = data.paystack_checkout;
          if (Object.prototype.hasOwnProperty.call(data, "paystack_checkout")) {
            checkoutUpdateRef.current = { userId, checkout: updatedCheckout };
            setBillingState(current => current
              ? { ...current, paystack_checkout: updatedCheckout }
              : current);
          }
          if (updatedCheckout?.status === "payment_pending") {
            setCheckoutStatus("payment_pending");
            return;
          }
          if (updatedCheckout?.status === "review" || (
            updatedCheckout?.reference && updatedCheckout.reference !== reference
          )) {
            setCheckoutStatus("review");
            return;
          }
          if (updatedCheckout?.status === "checkout_declined") {
            setCheckoutStatus("failed");
            return;
          }
          if ([
            "failed", "abandoned", "reversed", "retry_ready", "review",
            "payment_refunded", "account_deactivation_pending",
          ].includes(data.status)) {
            setCheckoutStatus(data.status);
            return;
          }
        } catch (caught) {
          if (controller.signal.aborted || caught?.name === "AbortError") return;
          const code = caught?.rawCode || caught?.code;
          if (caught?.status === 401) { setCheckoutStatus("sign_in_again"); return; }
          if (code === "account_deactivated" || code === "account_lifecycle_restricted") {
            setCheckoutStatus("account_deactivation_pending"); return;
          }
          if (isAccountOperationPending(caught) || !caught?.status || caught.status >= 500) continue;
          setCheckoutStatus("review");
          return;
        }
      }
      if (!controller.signal.aborted) setCheckoutStatus("pending");
    }
    void confirmPayment();
    return () => controller.abort();
  }, [
    authChecked, user?.id, checkoutReturn, checkoutReference, checkoutOutcome,
    confirmationAttempt, reloadAccount,
  ]);

  useEffect(() => {
    let cancelled = false;
    const requestId = ++billingRequestRef.current;
    const checkoutUpdate = checkoutUpdateRef.current;
    const controller = new AbortController();

    async function loadBilling() {
      if (!authChecked) return;

      if (!user) {
        loadedBillingUserRef.current = null;
        setBillingLoadError("");
        setLoading(false);
        setBillingState(null);
        return;
      }

      const initialLoad = loadedBillingUserRef.current !== user.id;
      if (initialLoad) {
        setLoading(true);
        setBillingState(null);
        setError("");
        setOrganizationName("");
      }
      setBillingLoadError("");

      try {
        const data = await loadBillingPlans(controller.signal);
        if (cancelled || requestId !== billingRequestRef.current) return;

        loadedBillingUserRef.current = user.id;
        const latestCheckout = checkoutUpdateRef.current;
        setBillingState(latestCheckout?.userId === user.id && checkoutUpdate !== latestCheckout
          ? { ...data, paystack_checkout: latestCheckout.checkout }
          : data);
        setOrganizationName(
          (current) =>
            current || String(data?.entitlement?.organization_name || ""),
        );
      } catch (caught) {
        if (cancelled || requestId !== billingRequestRef.current || caught?.name === "AbortError") return;

        if (isNotFoundError(caught)) {
          const fallbackState = buildFallbackBillingState({
            language,
            entitlement,
          });
          setBillingState(fallbackState);
          loadedBillingUserRef.current = user.id;
          setMessage(
            t.billingApiMissing ||
              FALLBACK_PLAN_COPY[language]?.apiMissing ||
              FALLBACK_PLAN_COPY.en.apiMissing,
          );
          return;
        }

        setBillingLoadError(getErrorMessage(caught, language, "BILLING_UNAVAILABLE"));
      } finally {
        if (!cancelled && requestId === billingRequestRef.current) setLoading(false);
      }
    }

    void loadBilling();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // Account refreshes return new object identities. The serialized account
    // value tracks actual entitlement changes without resetting purchase forms.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    authChecked,
    billingAccountKey,
    billingLoadAttempt,
    language,
    t.loadFailed,
    t.billingApiMissing,
  ]);

  useEffect(() => {
    if (!openCheckout?.reference || !retryScheduled || CHECKOUT_CHECK_UNAVAILABLE.has(checkoutStatus)) {
      return undefined;
    }
    const remaining = retryAtMs - Date.now();
    if (remaining > 0x7fffffff) return undefined;
    const reference = openCheckout.reference;
    const timer = window.setTimeout(() => {
      if (!mutationInProgress.current) {
        setCheckoutReturn({
          reference,
          returnTo: `${pathname}?reference=${encodeURIComponent(reference)}`,
        });
        setConfirmationAttempt(value => value + 1);
      }
    }, Math.max(1_000, remaining + 1_000));
    return () => window.clearTimeout(timer);
  }, [openCheckout?.reference, retryScheduled, retryAtMs, checkoutStatus, pathname]);

  async function handleUpgrade(targetPlan, selectedSeatCount) {
    if (mutationInProgress.current || checkoutInProgress) return;
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

    mutationInProgress.current = true;
    setBusyPlan(targetPlan);
    setError("");
    setMessage("");

    try {
      const data = await createBillingUpgradeIntent(targetPlan, {
        provider: "paystack",
        organizationName: isOrganizationPlan
          ? normalizedOrganizationName
          : undefined,
        seatCount: isOrganizationPlan ? seatSelection.seats : undefined,
      });

      if (data?.status === "checkout_pending" || data?.status === "payment_pending") {
        const checkout = { ...data, status: data.checkout_status || data.status };
        checkoutUpdateRef.current = { userId: user.id, checkout };
        setBillingState(current => current ? { ...current, paystack_checkout: checkout } : current);
        setCheckoutReturn(current => ({ ...current, reference: "", outcome: "" }));
        setCheckoutStatus("");
        setConfirmationAttempt(value => value + 1);
        return;
      }
      if (data?.checkout_url) {
        window.location.assign(data.checkout_url);
        return;
      }
      if (data?.upgrade_scheduled || data?.requires_checkout_at_period_end) {
        setMessage(planChangeMessage(data, language, returnMessage));
      } else if (!data?.success) {
        setError(returnMessage.checkoutUnavailable);
        return;
      } else {
        setMessage(getPageRuntimeCopy("billing", language).subscriptionUpdated);
      }
      await reloadAccount?.();
      const latest = await loadBillingPlans();
      setBillingState(latest);
    } catch (caught) {
      setError(isNotFoundError(caught)
        ? returnMessage.checkoutUnavailable
        : getErrorMessage(caught, language, "SUBSCRIPTION_OPERATION_FAILED"));
    } finally {
      mutationInProgress.current = false;
      setBusyPlan("");
    }
  }

  async function handleSubscriptionAction(action, targetPlan = null, scope = "current") {
    if (mutationInProgress.current) return;
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
    mutationInProgress.current = true;
    setBusyPlan(busyKey);
    setError("");
    setMessage("");

    try {
      const data = await manageBillingSubscription(action, { targetPlan, scope });
      setMessage(planChangeMessage(data, language, returnMessage));
      await reloadAccount?.();
      const latest = await loadBillingPlans();
      setBillingState(latest);
    } catch (caught) {
      setError(getErrorMessage(caught, language, "SUBSCRIPTION_OPERATION_FAILED"));
    } finally {
      mutationInProgress.current = false;
      setBusyPlan("");
    }
  }

  const personal = billingState?.personal_management;
  async function refreshPaidBilling() {
    const latest = await loadBillingPlans();
    setBillingState(latest);
    await reloadAccount?.({ background: true, forceRefresh: true });
  }

  const providers = billingState?.providers?.filter(provider => provider.key === "paystack") ||
    defaultProviderOptions(language, "paystack");

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

          {accountError && !user ? (
            <section className="mt-6 rounded-2xl border app-surface p-6"><AccountSessionRetry language={language} /></section>
          ) : !authChecked || loading || (!user && !checkoutReturn) ? (
            <section className="mt-6 rounded-3xl border app-surface-strong p-6" role="status">
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
                href={buildAuthLoginUrl({ language, returnTo: checkoutReturn?.returnTo || "/billing" })}
                className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]"
              >
                {t.signIn}
              </a>
            </section>
          ) : (
            <>
              {checkoutStatus || openCheckout ? (
                <section className="mt-6 rounded-2xl border app-surface p-4" role="status">
                  <p>{checkoutMessage}</p>
                  {retryScheduled && checkoutStatus !== "checking" ? (
                    <p className="mt-2 text-sm">
                      {returnMessage.retryAt.replace("{time}", new Date(retryAtMs).toLocaleString(language))}
                    </p>
                  ) : checkoutStatus === "failed" && openCheckout ? (
                    <p className="mt-2 text-sm">{returnMessage.retryUnknown}</p>
                  ) : null}
                  {showCheckoutLink ? (
                    <a className="mr-4 mt-2 inline-block underline" href={openCheckout.checkout_url}>
                      {returnMessage.resume}
                    </a>
                  ) : null}
                  {canCheckPayment ? (
                    <button type="button" className="mt-2 underline"
                      onClick={() => checkPayment(openCheckout?.reference || checkoutReference)}>
                      {returnMessage.check}
                    </button>
                  ) : null}
                  {checkoutStatus === "sign_in_again" ? (
                    <a className="mt-2 block underline" href={buildAuthLoginUrl({ language, returnTo: checkoutReturn?.returnTo })}>{t.signIn}</a>
                  ) : null}
                </section>
              ) : null}
              {billingLoadError ? (
                <div className="mt-6 rounded-3xl border app-surface p-4 text-sm" role="alert">
                  <p>{billingLoadError}</p>
                  <button type="button" className="mt-2 underline"
                    onClick={() => setBillingLoadAttempt(value => value + 1)}>
                    {returnMessage.retryPlans}
                  </button>
                </div>
              ) : null}
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

              <PaymentProvider
                providers={providers}
                providerCopy={providerCopy}
                language={language}
                unavailableLabel={returnMessage.plansUnavailable}
              />

              {personal && (personal.can_cancel || personal.can_resume || personal.collection?.job) ? (
                <section className="mt-6 rounded-2xl border app-surface p-5">
                  <h2 className="text-lg font-semibold">{returnMessage.personal}</h2>
                  {personal.current_period_end ? <p>{managementCopy.periodEnds} {new Date(personal.current_period_end).toLocaleDateString(language)}</p> : null}
                  {personal.can_cancel || personal.can_resume ? (
                    <button type="button" disabled={Boolean(busyPlan)} className="mt-3 rounded-xl border app-surface px-4 py-2"
                      onClick={() => handleSubscriptionAction(personal.can_resume ? "resume" : "cancel", null, "personal")}>
                      {personal.can_resume ? managementCopy.resume : managementCopy.cancel}
                    </button>
                  ) : null}
                  <BillingSeats key={`${user.id}:personal`} billingState={{ collection: personal.collection }} language={language} onPaid={refreshPaidBilling} scope="personal" />
                </section>
              ) : null}
              <BillingSeats key={user?.id} billingState={billingState} language={language} onPaid={refreshPaidBilling} />

              <section className="mt-6 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
                {(billingState?.plans || []).map((plan) => (
                  <PlanCard
                    key={`${user.id}:${plan.key}`}
                    plan={plan}
                    t={t}
                    providerCopy={providerCopy}
                    busyPlan={busyPlan || (checkoutStatus === "checking" ? "payment-check" : "")}
                    onUpgrade={handleUpgrade}
                    onDowngrade={(targetPlan) =>
                      handleSubscriptionAction("downgrade", targetPlan)
                    }
                    managementCopy={managementCopy}
                    periodLocked={Boolean(billingState?.management?.period_locked)}
                    checkoutInProgress={checkoutInProgress}
                    retryLabel={checkoutStatus === "retry_ready" ? returnMessage.startNew : undefined}
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
