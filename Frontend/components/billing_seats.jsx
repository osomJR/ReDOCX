"use client";
import { useEffect, useRef, useState } from "react";
import { getSeatChange, purchaseBillingSeats, getPaymentMethodUpdateUrl, retryBillingCollection } from "@/lib/api_client";

const COPY = {
  en: { title: "Organization seats", active: "Active seats", capacity: "Paid capacity this period", next: "Renewal charges use active members, including the owner. Pending invitations are not billed. Unused capacity expires at renewal.", add: "New total seat capacity", preview: "Review price", confirm: "Continue to secure payment", due: "Due now for the remaining paid period", per: "Per active seat at renewal", pending: "Payment confirmation is pending. Your current seats remain available.", paid: "Payment verified. Seat capacity has been updated.", resume: "Continue payment", method: "Update payment method", check: "Check payment", error: "Could not complete this request. Please retry.", review: "This payment needs support review. Please do not pay again.", retry: "Next automatic retry", help: "Added seats become available after verified payment. Remove members to reduce the next renewal; your plan stays the same." },
  fr: { title: "Places de l’organisation", active: "Places actives", capacity: "Capacité payée pour cette période", next: "Le renouvellement facture les membres actifs, propriétaire compris. Les invitations en attente ne sont pas facturées. La capacité inutilisée expire au renouvellement.", add: "Nouvelle capacité totale", preview: "Vérifier le prix", confirm: "Continuer vers le paiement sécurisé", due: "À payer pour le reste de la période", per: "Par place active au renouvellement", pending: "Confirmation du paiement en attente. Vos places actuelles restent disponibles.", paid: "Paiement vérifié. La capacité a été mise à jour.", resume: "Continuer le paiement", method: "Mettre à jour le moyen de paiement", check: "Vérifier le paiement", error: "Impossible de terminer cette demande. Réessayez.", review: "Ce paiement nécessite une vérification du support. Ne payez pas à nouveau.", retry: "Prochaine tentative automatique", help: "Les places sont disponibles après vérification du paiement. Retirez des membres pour réduire le prochain renouvellement ; votre offre reste inchangée." },
};

const money = value => new Intl.NumberFormat("en-NG", { style: "currency", currency: "NGN" }).format(value / 100);

export default function BillingSeats({ billingState, language, onPaid }) {
  const t = COPY[language] || COPY.en;
  const collection = billingState?.collection;
  const orgId = collection?.organization_id || billingState?.entitlement?.organization_id;
  const [input, setInput] = useState("");
  const [quote, setQuote] = useState(null);
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const keyRef = useRef("");
  const paidRef = useRef(null);
  const onPaidRef = useRef(onPaid);
  useEffect(() => { onPaidRef.current = onPaid; }, [onPaid]);
  const initialJobId = collection?.job?.id;

  useEffect(() => {
    if (collection?.job) setJob(collection.job);
    // Only initialize a newly reported job, preserving an in-progress checkout.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialJobId]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("seat_job") || job?.id;
    if (!id || job?.status === "paid" || job?.status === "review") return undefined;
    const controller = new AbortController();
    let timer;
    let count = 0;
    async function poll() {
      if (document.visibilityState === "hidden") {
        timer = setTimeout(poll, 5000);
        return;
      }
      try {
        const data = await getSeatChange({ jobId: id, signal: controller.signal });
        if (controller.signal.aborted) return;
        setJob(data);
        if (data.status === "paid") {
          setQuote(null);
          setInput("");
          if (paidRef.current !== data.id) {
            paidRef.current = data.id;
            await onPaidRef.current?.();
          }
          const url = new URL(window.location.href);
          ["seat_job", "reference", "trxref"].forEach(key => url.searchParams.delete(key));
          window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
          return;
        }
        if (!["open", "pending"].includes(data.status)) return;
      } catch (caught) {
        if (caught?.name === "AbortError") return;
      }
      if (++count < 36 && !controller.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status]);

  async function preview() {
    setBusy(true); setError(""); setQuote(null);
    try {
      const seats = Number(input);
      if (!Number.isSafeInteger(seats) || seats <= Number(collection?.paid_seats || 0)) throw new Error(t.help);
      const data = await getSeatChange({ organizationId: orgId, seatCount: seats });
      keyRef.current = crypto.randomUUID();
      setQuote(data);
    } catch (caught) { setError(caught?.payload?.detail?.message || (typeof caught?.payload?.detail === "string" ? caught.payload.detail : t.error)); }
    finally { setBusy(false); }
  }

  async function pay() {
    setBusy(true); setError("");
    try {
      const data = await purchaseBillingSeats(quote, keyRef.current);
      setJob(data);
      if (data.checkout_url) window.location.assign(data.checkout_url);
    } catch (caught) { setError(caught?.payload?.detail?.message || t.error); }
    finally { setBusy(false); }
  }

  const shownJob = job || collection?.job;
  if (!collection?.can_add_seats && !shownJob) return null;
  const locked = busy || ["open", "pending", "review"].includes(shownJob?.status);
  const button = "rounded-xl border border-[var(--app-border)] px-4 py-3 text-sm font-semibold disabled:opacity-50";
  return <section className="mt-6 rounded-3xl border app-surface-strong p-6" aria-labelledby="billing-seats-title">
    <h2 id="billing-seats-title" className="text-lg font-semibold">{orgId ? t.title : (language === "fr" ? "Paiement du renouvellement" : "Renewal payment")}</h2>
    {orgId ? <>
      <p className="mt-2 text-sm">{t.active}: {collection?.active_seats ?? "—"} · {t.capacity}: {collection?.paid_seats ?? "—"}</p>
      <p className="mt-2 text-sm app-text-muted">{t.help}</p>
      {collection?.managed ? <p className="mt-2 text-sm app-text-muted">{t.next}</p> : null}
    </> : null}
    {error ? <p role="alert" className="mt-3 text-red-300">{error}</p> : null}
    {shownJob ? <div role="status" className="mt-4 text-sm">
      <p>{shownJob.status === "paid" ? (shownJob.purpose === "seats" ? t.paid : (language === "fr" ? "Renouvellement payé et vérifié." : "Renewal payment verified.")) : shownJob.status === "review" ? t.review : shownJob.message || t.pending}</p>
      {shownJob.purpose === "renewal" ? <p className="mt-2">{money(shownJob.amount)} · {shownJob.quantity} {t.active.toLowerCase()}</p> : null}
      {shownJob.next_attempt_at ? <p>{t.retry}: {new Date(shownJob.next_attempt_at).toLocaleString(language)}</p> : null}
      {shownJob.checkout_url ? <a className={`mt-3 inline-block ${button}`} href={shownJob.checkout_url}>{t.resume}</a> : null}
      <button className={`ml-2 ${button}`} disabled={busy} onClick={async () => {
        setBusy(true);
        try { const data = await getSeatChange({ jobId: shownJob.id }); setJob(data); if (data.status === "paid") await onPaidRef.current?.(); }
        catch { setError(t.error); } finally { setBusy(false); }
      }}>{t.check}</button>
    </div> : null}
    {collection?.can_add_seats ? <div className="mt-5 flex flex-wrap items-end gap-3">
      <label className="text-sm" htmlFor="additional-seat-capacity">{t.add}
        <input id="additional-seat-capacity" type="number" min={Number(collection.paid_seats || 0) + 1}
          max={billingState?.current_plan === "business" ? 19 : undefined} step="1" value={input} disabled={locked}
          onChange={event => { setInput(event.target.value); setQuote(null); }}
          className="mt-2 block w-40 rounded-xl border border-[var(--app-border)] bg-transparent p-3" />
      </label>
      <button className={button} disabled={locked || !input} onClick={preview}>{t.preview}</button>
    </div> : null}
    {quote ? <div className="mt-4 rounded-xl border border-[var(--app-border)] p-4">
      <p>{t.due}: <strong>{money(quote.amount)}</strong></p>
      <p className="mt-2 text-sm">{t.per}: {money(quote.unit_amount)}</p>
      <p className="mt-2 text-sm app-text-muted">{t.next}</p>
      <button className={`mt-4 ${button}`} disabled={locked} onClick={pay}>{t.confirm}</button>
    </div> : null}
    {shownJob?.status === "requires_action" && !collection?.managed ? <button className={`mt-4 ${button}`} disabled={busy} onClick={async () => {
      try { const data = await getPaymentMethodUpdateUrl(); window.location.assign(data.url); }
      catch { setError(t.error); }
    }}>{t.method}</button> : null}
    {shownJob?.status === "requires_action" && !collection?.managed ? <button className={`ml-2 mt-4 ${button}`} disabled={busy} onClick={async () => {
      setBusy(true);
      try { setJob(await retryBillingCollection(shownJob.id)); }
      catch (caught) { setError(caught?.payload?.detail?.message || t.error); }
      finally { setBusy(false); }
    }}>{language === "fr" ? "Réessayer après la mise à jour" : "Retry after updating"}</button> : null}
    {shownJob?.status === "requires_action" && collection?.managed && shownJob.purpose === "renewal" ? <div className="mt-4">
      <p className="text-sm app-text-muted">{language === "fr" ? "Le nouveau moyen de paiement servira aussi aux prochains renouvellements. Vous pouvez annuler le renouvellement sur cette page." : "The payment method used here will also be used for future renewals. You can cancel renewal on this page."}</p>
      <button className={`mt-3 ${button}`} disabled={busy} onClick={async () => {
        setBusy(true); setError("");
        try {
          const data = await retryBillingCollection(shownJob.id, "checkout");
          setJob(data);
          if (data.checkout_url) window.location.assign(data.checkout_url);
        } catch (caught) { setError(caught?.payload?.detail?.message || (typeof caught?.payload?.detail === "string" ? caught.payload.detail : t.error)); }
        finally { setBusy(false); }
      }}>{t.confirm} · {money(shownJob.amount)}</button>
    </div> : null}
  </section>;
}
