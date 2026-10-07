import { fetchWithAuth } from '@/utils/fetchWithAuth';
import React, { useState, useEffect, useCallback } from 'react';
import { createGovernedRef, createPrimaryContext, createNestedContext } from '@/utils/governedNavigation';
import { GovernedWorkspace } from '@/components/GovernedUI/GovernedWorkspace';
import { RecommendationReview } from '@/components/GovernedUI/RecommendationReview';
import { ContextPanel } from '@/components/GovernedUI/ContextPanel';
import { ProgressiveSection } from '@/components/GovernedUI/ProgressiveSection';
import { createPortal } from 'react-dom';
import { Activity, Phone, Mail, Calendar, ChevronRight, X, Clock, Database, Sparkles, Check, Shield, Search, Filter, Cpu, CheckCircle2, ShieldCheck, RefreshCw, AlertTriangle, AlertCircle, ArrowUpRight, Copy, BookOpen, Zap } from 'lucide-react';
import { useSelector, useDispatch } from 'react-redux';
import { RootState } from '@/store';
import { incrementActionsTaken, SignalEvent } from '@/store/slices/signalSlice';
import { GovernedStatus } from '@/components/GovernedUI/GovernedStatus';
import { DataState } from '@/components/GovernedUI/DataState';
import { CriticalStateBanner } from '@/components/GovernedUI/CriticalStateBanner';
import { fetchDecisionBasis } from '@/utils/decisionBasis';
import type { DecisionBasisDTO } from '@/types/decisionBasis';
import { DecisionContextSummary } from '@/components/DecisionBasis/DecisionContextSummary';
import { EvidenceSummary } from '@/components/DecisionBasis/EvidenceSummary';
import { PolicySummary } from '@/components/DecisionBasis/PolicySummary';
import { RuleResultList } from '@/components/DecisionBasis/RuleResultList';
import { ProvenanceDetail } from '@/components/DecisionBasis/ProvenanceDetail';
// D6.10: Action Lifecycle
import { ActionLifecycleSection } from '@/components/Action/ActionLifecycleSection';
// D8: Historical Trace
import { HistoricalTraceSection } from '@/components/History/HistoricalTraceSection';
// D7: Runtime Status
import { fetchRuntimeStatus } from '@/utils/runtimeStatus';
import { RuntimeStatusDTO } from '@/types/runtimeStatus';
import { mapStateToSemantic } from '@/utils/failurePresentation';
import { FailureNotice } from '@/components/GovernedUI/FailureNotice';
import { DegradedStateBanner } from '@/components/GovernedUI/DegradedStateBanner';

export const SignalsDetailView: React.FC<{ initialSignalId?: string | null }> = ({ initialSignalId }) => {
  const dispatch = useDispatch();
  const reduxSignals = useSelector((state: RootState) => state.signals.events);
  const [dbSignals, setDbSignals] = useState<SignalEvent[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [activeFilter, setActiveFilter] = useState<'All' | 'EHR' | 'Phone' | 'Email'>('All');
  const [selectedSignal, setSelectedSignal] = useState<SignalEvent | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [viewMode, setViewMode] = useState<'doctor' | 'developer' | 'inspector'>('doctor');
  const [isProd, setIsProd] = useState(true);
  // D4 — Decision Basis state
  const [basisData, setBasisData] = useState<DecisionBasisDTO | null>(null);
  const [basisLoading, setBasisLoading] = useState(false);
  const [highlightedRuleId, setHighlightedRuleId] = useState<string | null>(null);
  // D6.10 — Current decision_id for Action Lifecycle, propagated from RecommendationReview
  const [currentDecisionId, setCurrentDecisionId] = useState<string | null>(null);
  
  // D7 — Runtime Status
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatusDTO | null>(null);

  // Combine redux state and db historical signals
  const fetchDbSignals = async () => {
    try {
      setIsRefreshing(true);
      const response = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/signals`);
      if (response.ok) {
        const data = await response.json();
        setDbSignals(data);
      }
    } catch (err) {
      console.error("Failed to load historical signals:", err);
    } finally {
      setIsRefreshing(false);
    }
  };
  const fetchAuditLogs = async () => {
    try {
      const response = await fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/audit/`);
      if (response.ok) {
        const data = await response.json();
        const filtered = data.filter((log: any) => log.action.startsWith('Approved Sentinel Action'));
        setAuditLogs(filtered);
      }
    } catch (err) {
      console.error("Failed to load dispatched actions log:", err);
    }
  };

  const loadRuntimeStatus = async () => {
    try {
      const status = await fetchRuntimeStatus();
      setRuntimeStatus(status);
    } catch (e) {
      console.error("Failed to load runtime status:", e);
      // Construct a safe UNAVAILABLE state
      setRuntimeStatus({
        overall: { scope: 'system', state: 'UNAVAILABLE', message: 'Failed to communicate with runtime server.' },
        capabilities: [],
        connectors: [],
        technical_state: 'unavailable'
      });
    }
  };

  useEffect(() => {
    fetchDbSignals();
    fetchAuditLogs();
    loadRuntimeStatus();
    // Audit 3 Item 8 / Audit 4 Item 6: Demo State gating
    fetchWithAuth(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/settings`)
      .then(res => res.json())
      .then(data => {
        // Explicitly require opt-in to demo mode
        if (data.ENVIRONMENT !== 'PRODUCTION' && data.SYNTHETIC_TEST_ENABLED) {
          setIsProd(false);
        }
      })
      .catch(() => {
        // Fail open to safe (production) mode
        setIsProd(true);
      });
  }, [reduxSignals]);
  const allSignalsMap = new Map<string, SignalEvent>();
  dbSignals.forEach(s => allSignalsMap.set(s.id, s));
  reduxSignals.forEach(s => allSignalsMap.set(s.id, s));
  const signalsList = Array.from(allSignalsMap.values()).sort((a, b) =>
    new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );
  useEffect(() => {
    if (initialSignalId) {
      const sig = allSignalsMap.get(initialSignalId);
      if (sig) {
        setSelectedSignal(sig);
      }
    }
  }, [initialSignalId, dbSignals, reduxSignals]);
  const loadDecisionBasis = useCallback(async (signalId: string) => {
    setBasisData(null);
    setBasisLoading(true);
    try {
      const data = await fetchDecisionBasis(signalId);
      setBasisData(data);
    } finally {
      setBasisLoading(false);
    }
  }, []);
  useEffect(() => {
    if (selectedSignal) {
      setCurrentDecisionId(null);
      loadDecisionBasis(selectedSignal.id);
    } else {
      setCurrentDecisionId(null);
      setBasisData(null);
      setBasisLoading(false);
    }
  }, [selectedSignal, loadDecisionBasis]);
  const getIcon = (type: string) => {
    switch (type) {
      case 'EHR': return <Database className="w-4 h-4 text-[var(--color-semantic-attention)]" />;
      case 'Phone': return <Phone className="w-4 h-4 text-[var(--color-semantic-critical)]" />;
      case 'Email': return <Mail className="w-4 h-4 text-[var(--color-accent)]" />;
      default: return <Activity className="w-4 h-4 text-[var(--color-semantic-positive)]" />;
    }
  };
  const getBgColor = (type: string) => {
    switch (type) {
      case 'EHR': return 'bg-[var(--color-semantic-attention)]/20 text-[var(--color-semantic-attention)] border border-[#FDE68A]';
      case 'Phone': return 'bg-[var(--color-semantic-critical)]/20 text-[var(--color-semantic-critical)] border border-[#FCA5A5]';
      case 'Email': return 'bg-[var(--color-accent)]/20 text-[var(--color-accent)] border border-[#BFDBFE]';
      default: return 'bg-[#D1FAE5] text-[var(--color-semantic-positive)] border border-[#A7F3D0]';
    }
  };
  const handleCopyId = (id: string) => {
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Filtered list
  const filteredSignals = signalsList.filter(s => {
    const matchesSearch = s.message.toLowerCase().includes(searchTerm.toLowerCase()) ||
      s.source.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (s.metadata?.patient_name || '').toLowerCase().includes(searchTerm.toLowerCase());
    const matchesType = activeFilter === 'All' ? true : s.type === activeFilter;
    return matchesSearch && matchesType;
  });

  // Calculate metrics
  const totalCount = signalsList.length;
  const ehrCount = signalsList.filter(s => s.type === 'EHR').length;
  const phoneCount = signalsList.filter(s => s.type === 'Phone').length;
  const emailCount = signalsList.filter(s => s.type === 'Email').length;
  const lossRiskCount = signalsList.filter(s => s.risk_level === 'Critical' || s.risk_level === 'High').length;



  // Generate simulated Practice Fusion / twilio raw payload details for audit
  const getSimulatedRawPayload = (signal: SignalEvent) => {
    // Issue #5: Only simulate in demo environments.
    if (isProd) return signal;
    const patientName = signal.metadata?.patient_name || "Unknown Patient";
    const cleanPatient = patientName.replace(/\s+/g, '').toLowerCase();

    if (signal.type === 'EHR') {
      return {
        resourceType: "Appointment",
        id: `fhir-${signal.id}`,
        status: "noshow",
        serviceCategory: {
          coding: [{ system: "http://snomed.info/sct", code: "408443003", display: "General practice" }]
        },
        specialty: [{
          coding: [{ system: "http://snomed.info/sct", code: "394814009", display: "General practice clinic" }]
        }],
        appointmentType: {
          coding: [{ system: "http://terminology.hl7.org/CodeSystem/v2-0276", code: "WALKIN", display: "Walk in appointment" }]
        },
        patient: {
          reference: `Patient/${cleanPatient}-9021`,
          display: patientName,
          contact: {
            email: `${cleanPatient}@sbnsentinel-demo.com`,
            phone: "+1 (555) 438-9210"
          }
        },
        provider: {
          reference: "Practitioner/dr-smith-223",
          display: "Dr. Robert Smith"
        },
        schedule: {
          planned_start: signal.timestamp,
          wait_duration_minutes: signal.message.includes('45 minutes') ? 45 : 15
        },
        audit_trail: {
          practise_fusion_client_id: "pf-oauth-client-88123-prod",
          api_version: "Secure Data R4",
          authorization_scope: "user/Appointment.write patient/Patient.read"
        }
      };
    } else if (signal.type === 'Phone') {
      return {
        event_type: "missed_call",
        id: `twilio-${signal.id}`,
        provider: "Twilio Voice API",
        account_sid: "AC_MOCK_ACCOUNT_SID_1234567890",
        call_sid: `CA${uuidSim(24)}`,
        from: "+15550198",
        to: "+18005553190",
        duration_seconds: 0,
        status: "no-answer",
        direction: "inbound",
        patient_matched: {
          reference: `Patient/${cleanPatient}-9021`,
          name: patientName,
          email: `${cleanPatient}@sbnsentinel-demo.com`,
          insurance_id: "INS-99210-A"
        },
        webhook_meta: {
          url: "https://api.sbnsentinel.com/v1/signals/twilio/webhook",
          signature: "sha256-aef8912cf31"
        }
      };
    } else {
      return {
        message_id: `outlook-msg-${signal.id}@office365.microsoft.com`,
        subject: `Diagnostic Report: ${patientName}`,
        from: "notifications@labcorp-secure.com",
        to: "dr.smith@sbnsentinel-demo.com",
        received_time: signal.timestamp,
        has_attachments: true,
        attachments: [{
          name: `${cleanPatient}_lab_result_cbc_cmp.pdf`,
          mime_type: "application/pdf",
          size_bytes: 409600
        }],
        nlp_extraction: {
          patient_name: patientName,
          critical_finding: false,
          diagnostic_category: "Hematology & Chemistry Panel"
        },
        security: {
          dkim_verification: "PASS",
          spf_verification: "PASS",
          security_compliance_check: "SECURE"
        }
      };
    }
  };
  const uuidSim = (len: number) => {
    let result = '';
    const chars = 'abcdef0123456789';
    for (let i = 0; i < len; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
  };
  if (selectedSignal) {
    const signalRef = createGovernedRef('Signal', selectedSignal.id);
    const primaryCtx = createPrimaryContext(signalRef, selectedSignal.metadata?.is_historical ? 'historical' : 'current');
    const nestedCtx = createNestedContext(signalRef, 2, signalRef, primaryCtx.mode);
    const primaryContent = (
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
          <div className={`p-2.5 ${getBgColor(selectedSignal.type)} rounded-[16px] flex-shrink-0`}>
            {getIcon(selectedSignal.type)}
          </div>
          <div>
            <h4 className="text-base font-extrabold text-white flex items-center gap-2">
              Signal Diagnostic Report
              {selectedSignal.correlation_id && (
                <span className="text-[10px] font-mono bg-blue-500/20 border border-blue-500/50 text-blue-400 px-2 py-0.5 rounded-[6px]">
                  Journey ID: {selectedSignal.correlation_id}
                </span>
              )}
            </h4>
            <p className="text-[10px] text-white/70 font-extrabold uppercase tracking-widest mt-0.5">Source: {selectedSignal.source} Integration Layer</p>
          </div>
        </div>
        <div className="bg-white/5 border border-white/10 rounded-[18px] p-4">
          <h5 className="text-[10px] font-extrabold text-white/50 uppercase tracking-widest mb-1.5">TELEMETRY MESSAGE</h5>
          <p className="text-sm font-bold text-white">{selectedSignal.message}</p>
          <div className="flex items-center gap-3 mt-3 text-[11px] text-white/70 font-semibold">
            <span>Matched Patient: <strong className="text-white">{selectedSignal.metadata?.patient_name || 'None'}</strong></span>
            <span>•</span>
            <span>Received: <strong className="text-white">{new Date(selectedSignal.timestamp).toLocaleString()}</strong></span>
          </div>
        </div>
        <div className="bg-white/5 border border-white/10 rounded-[18px] p-4 flex gap-3">
          <div className="flex-1">
            <h5 className="text-xs font-extrabold text-white uppercase tracking-wider mb-2">Deterministic Evaluation</h5>
            <div className="space-y-2 mb-3">
              <p className="text-xs text-white/90 font-semibold"><span className="text-white/50">Problem:</span> {selectedSignal.problem || 'None'}</p>
              <p className="text-xs text-white/90 font-semibold"><span className="text-white/50">Reason:</span> {selectedSignal.reason || 'None'}</p>
            </div>

            <div className="mt-8">
              <RecommendationReview
                signalId={selectedSignal.id}
                onDecisionChange={setCurrentDecisionId}
                onViewBasis={(evaluationId) => {
                  setHighlightedRuleId(evaluationId);
                  const el = document.getElementById('decision-basis');
                  if (el) {
                    const btn = el.querySelector('button[aria-expanded="false"]');
                    if (btn) (btn as HTMLElement).click();
                    el.scrollIntoView({ behavior: 'smooth' });
                    setTimeout(() => {
                      const ruleEl = document.getElementById(`rule-eval-${evaluationId}`);
                      if (ruleEl) ruleEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 300);
                  }
                }}
              />
              
              {/* D6.10 — Action Lifecycle Workflow */}
              {currentDecisionId && (
                <div className="mt-8 border-t border-white/10 pt-6">
                  <ActionLifecycleSection decisionId={currentDecisionId} />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
    const contextPanels = (
      <>
        <ContextPanel
          context={nestedCtx}
          title="Context & Intelligence"
          icon={<Sparkles className="w-4 h-4 text-emerald-400" />}
        >
          <div className="space-y-4">
            <ProgressiveSection
              id="ctx-engine"
              title="Decision Context Engine"
              icon={<Activity className="w-4 h-4 text-[var(--color-accent)]" />}
              defaultExpanded={true}
            >
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Primary Context</p>
                  <p className="text-xs font-bold text-white">{selectedSignal.primary_context || 'Unknown'}</p>
                </div>
                <div>
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Secondary Context</p>
                  <p className="text-xs font-bold text-white">{selectedSignal.secondary_context || 'Unknown'}</p>
                </div>
                <div className="col-span-2">
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Reasoning</p>
                  <p className="text-[11px] font-semibold text-white/70 leading-snug">{selectedSignal.context_reason || 'N/A'}</p>
                </div>
              </div>
            </ProgressiveSection>
            <ProgressiveSection
              id="rev-engine"
              title="Revenue Intelligence"
              icon={<Shield className="w-4 h-4 text-amber-400" />}
            >
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Risk Category</p>
                  <p className="text-xs font-bold text-white">{selectedSignal.revenue_risk_category || 'None'}</p>
                </div>
                <div>
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Financial Exposure</p>
                  <p className="text-sm font-black text-amber-400">{selectedSignal.estimated_financial_exposure || '$0.00'}</p>
                </div>
                <div className="col-span-2">
                  <p className="text-[10px] text-white/50 uppercase tracking-widest font-bold">Operational Dependency</p>
                  <p className="text-[11px] font-semibold text-white/70 leading-snug">{selectedSignal.operational_dependency || 'N/A'}</p>
                </div>
              </div>
            </ProgressiveSection>
            <ProgressiveSection
              id="raw-payload"
              title="Evidence Inspector & Raw Logs"
              icon={<Database className="w-4 h-4 text-blue-400" />}
            >
              <div className="bg-black/50 rounded-[12px] p-4 text-[10px] font-mono text-amber-400 overflow-x-auto max-h-48 custom-scrollbar border border-white/10">
                <pre>{JSON.stringify({
                  decision_context_id: `ctx-${selectedSignal.id}`,
                  evidence_snapshot: getSimulatedRawPayload(selectedSignal)
                }, null, 2)}</pre>
              </div>
            </ProgressiveSection>

            {/* D4 — Decision Basis (read-only projection) */}
            <ProgressiveSection
              id="decision-basis"
              title="Decision Basis"
              icon={<BookOpen className="w-4 h-4 text-violet-400" />}
              defaultExpanded={false}
              dataState={
                basisLoading
                  ? 'loading'
                  : basisData === null
                    ? 'unavailable'
                    : basisData.technical_state === 'unauthorized'
                      ? 'unauthorized'
                      : basisData.technical_state === 'unavailable'
                        ? 'unavailable'
                        : 'ready'
              }
              dataStateMessage={
                basisData?.technical_state === 'unauthorized'
                  ? 'You do not have permission to view the Decision Basis for this signal.'
                  : basisLoading
                    ? undefined
                    : 'Decision Basis detail is unavailable for this signal.'
              }
            >
              {basisData && basisData.technical_state === 'ready' && (
                <div className="space-y-6 pt-2">
                  {/* Level 1 — Decision Context Summary */}
                  <DecisionContextSummary context={basisData.decision_context} />

                  <div className="border-t border-white/10" />

                  {/* Level 2 — Evidence (Used / Missing / Conflicts / Freshness) */}
                  <EvidenceSummary evidence={basisData.evidence} />

                  <div className="border-t border-white/10" />

                  {/* Policy Basis */}
                  <PolicySummary policy={basisData.policy} />

                  <div className="border-t border-white/10" />

                  {/* Rule Evaluations */}
                  <RuleResultList rules={basisData.rules} highlightEvaluationId={highlightedRuleId || undefined} />

                  {/* Level 3 — Provenance (collapsed by default) */}
                  {basisData.provenance && (
                    <>
                      <div className="border-t border-white/10" />
                      <ProvenanceDetail provenance={basisData.provenance} />
                    </>
                  )}

                  {/* Journey ID for traceability */}
                  {basisData.journey_id && (
                    <p className="text-[10px] font-mono text-white/30 border-t border-white/10 pt-3">
                      Journey: {basisData.journey_id}
                    </p>
                  )}
                </div>
              )}
            </ProgressiveSection>

            {/* D8 — Historical Trace & Reproducibility */}
            {(() => {
              const journeyId = basisData?.journey_id ?? selectedSignal.correlation_id;
              if (!journeyId) return <div className="text-gray-400 p-4">No journey context available</div>;
              return <HistoricalTraceSection journeyId={journeyId} />;
            })()}
          </div>
        </ContextPanel>
      </>
    );
    return (
      <div className="h-[85vh] min-h-[600px] flex flex-col p-2">
        <GovernedWorkspace
          context={primaryCtx}
          primaryContent={primaryContent}
          contextPanels={contextPanels}
          onClose={() => setSelectedSignal(null)}
        />
      </div>
    );
  }
  return (
    <div className="animate-in fade-in duration-500 max-w-[1600px] mx-auto space-y-8">
      {/* D7 Banner for overall degraded state */}
      {runtimeStatus?.overall.state === 'DEGRADED' && (
        <DegradedStateBanner message={runtimeStatus.overall.message || "The system is currently operating in a degraded state."} />
      )}
      {runtimeStatus?.overall.state === 'UNAVAILABLE' && (
        <FailureNotice 
          title="System Unavailable" 
          affected="Overall System" 
          available="Cached data only" 
          timestamp={runtimeStatus.overall.checked_at}
          onRetry={loadRuntimeStatus}
        />
      )}

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 md:gap-0 relative">
        <div>
          <h2 className="text-3xl font-extrabold text-white mb-1">Signals Intelligence Hub</h2>
          <p className="text-sm text-white/70 font-medium">Real-time Secure Data EHR connections, Twilio hooks, and automated audit stream logs.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto">
          <button
            onClick={fetchDbSignals}
            disabled={isRefreshing}
            className="flex items-center gap-2 bg-white/10 border border-white/20 text-white font-bold text-xs px-4 py-2.5 rounded-[16px] hover:bg-white/20 transition-colors disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 text-white/70 ${isRefreshing ? 'animate-spin' : ''}`} /> Sync Database
          </button>
        </div>
      </div>
      {/* Metrics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 p-6 rounded-[24px] shadow-[0_20px_50px_rgba(46,16,85,0.3)] flex items-center justify-between text-white">
          <div>
            <p className="text-xs font-bold text-white/70 uppercase tracking-widest mb-1">Total Signals</p>
            <p className="text-2xl font-black text-white">{isProd ? totalCount : 1284 + totalCount}</p>
          </div>
          <span className="p-3 bg-[var(--color-accent)]/20 text-[var(--color-accent)] rounded-[16px]">
            <Cpu className="w-6 h-6" />
          </span>
        </div>

        <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 p-6 rounded-[24px] shadow-[0_20px_50px_rgba(46,16,85,0.3)] flex items-center justify-between text-white">
          <div>
            <p className="text-xs font-bold text-white/70 uppercase tracking-widest mb-1">EHR (Practice Fusion)</p>
            <p className="text-2xl font-black text-[var(--color-semantic-attention)]">{ehrCount} Active</p>
          </div>
          <span className="p-3 bg-[var(--color-semantic-attention)]/20 text-[var(--color-semantic-attention)] rounded-[16px]">
            <Database className="w-6 h-6" />
          </span>
        </div>
        <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 p-6 rounded-[24px] shadow-[0_20px_50px_rgba(46,16,85,0.3)] flex items-center justify-between text-white">
          <div>
            <p className="text-xs font-bold text-white/70 uppercase tracking-widest mb-1">Communication Logs</p>
            <p className="text-2xl font-black text-[var(--color-accent)]">{phoneCount + emailCount} Transmitted</p>
          </div>
          <span className="p-3 bg-[var(--color-accent)]/20 text-[var(--color-accent)] rounded-[16px]">
            <Mail className="w-6 h-6" />
          </span>
        </div>
        <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 p-6 rounded-[24px] shadow-[0_20px_50px_rgba(46,16,85,0.3)] flex items-center justify-between text-white">
          <div>
            <p className="text-xs font-bold text-white/70 uppercase tracking-widest mb-1">Revenue Risk Triggers</p>
            <p className="text-2xl font-black text-[var(--color-semantic-critical)]">{lossRiskCount} Flagged</p>
          </div>
          <span className="p-3 bg-[var(--color-semantic-critical)]/20 text-[var(--color-semantic-critical)] rounded-[16px]">
            <AlertTriangle className="w-6 h-6 animate-pulse" />
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Signals Feed Table */}
        <div className="lg:col-span-2 bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
            <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-[14px] px-3.5 py-2 w-72">
              <Search className="w-4 h-4 text-white/70" />
              <input
                type="text"
                placeholder="Search patient, source, or text..."
                value={searchTerm}
                onChange={e => setSearchTerm(e.target.value)}
                className="bg-transparent border-none outline-none text-xs text-white w-full placeholder:text-[#9CA3AF]"
              />
            </div>

            <div className="flex gap-2">
              {(['All', 'EHR', 'Phone', 'Email'] as const).map((filter) => (
                <button
                  key={filter}
                  onClick={() => setActiveFilter(filter)}
                  className={`px-3 py-1.5 rounded-[10px] text-xs font-bold transition-all border cursor-pointer ${activeFilter === filter
                    ? 'bg-white/20 text-white border-white/40 shadow-md'
                    : 'bg-white/5 text-white/70 border-white/10 hover:bg-white/10'
                    }`}
                >
                  {filter}
                </button>
              ))}
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 text-[11px] uppercase text-white/70 tracking-wider">
                  <th className="pb-4 font-extrabold px-2">ID</th>
                  <th className="pb-4 font-extrabold px-2">Channel / Patient</th>
                  <th className="pb-4 font-extrabold px-2">Message</th>
                  <th className="pb-4 font-extrabold px-2">Status</th>
                  <th className="pb-4 font-extrabold px-2 text-right">Payload</th>
                </tr>
              </thead>
              <tbody className="text-sm font-semibold text-white">
                {filteredSignals.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-8">
                      <DataState state="empty" message="No matching signals active in database." className="bg-transparent border-0" />
                    </td>
                  </tr>
                ) : (
                  filteredSignals.map((signal) => {
                    const isRisk = signal.risk_level === 'Critical' || signal.risk_level === 'High';
                    return (
                      <tr
                        key={signal.id}
                        onClick={() => {
                          setSelectedSignal(signal);
                        }}
                        className="border-b border-white/10 hover:bg-white/5 transition-all last:border-0 cursor-pointer"
                      >
                        <td className="py-4 px-2 font-mono text-[11px] text-[#2563EB]">{signal.id}</td>
                        <td className="py-4 px-2">
                          <span className={`inline-flex items-center gap-1.5 text-[9px] font-extrabold px-2 py-0.5 rounded-full uppercase tracking-wider mb-1 ${getBgColor(signal.type)}`}>
                            {getIcon(signal.type)}
                            {signal.source}
                          </span>
                          <p className="text-xs text-white/70 font-bold mt-0.5">Patient: {signal.metadata?.patient_name || 'N/A'}</p>
                        </td>
                        <td className="py-4 px-2 max-w-xs">
                          <p className="text-sm font-bold text-white line-clamp-2 leading-snug">{signal.message}</p>
                          <p className="text-[10px] text-white/50 uppercase mt-1 flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {new Date(signal.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                          </p>
                        </td>
                        <td className="py-4 px-2">
                          {isRisk ? (
                            <span className="inline-flex items-center gap-1 text-[10px] bg-red-500/20 text-red-400 border border-red-500/30 px-2 py-0.5 rounded-[8px] font-extrabold uppercase">
                              <AlertCircle className="w-3 h-3" /> Risk Flagged
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-[8px] font-extrabold uppercase">
                              <ShieldCheck className="w-3 h-3" /> Normal
                            </span>
                          )}
                        </td>
                        <td className="py-4 px-2 text-right">
                          <button className="text-[11px] font-bold text-white bg-white/10 hover:bg-white/20 px-2.5 py-1.5 rounded-[8px] transition-colors inline-flex items-center gap-1 ml-auto">
                            Inspect <ArrowUpRight className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Diagnostic Panel Sidebar */}
        <div className="flex flex-col gap-6">
          {/* SESR-009 System Capability Matrix */}
          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <h3 className="text-base font-extrabold text-white mb-6 flex items-center gap-2">
              <Activity className="w-5 h-5 text-[var(--color-accent)]" /> System Capability Matrix
            </h3>
            <div className="space-y-4">
              {runtimeStatus?.capabilities.map(cap => {
                const semantic = mapStateToSemantic(cap);
                const isCritical = semantic.semantic === 'critical';
                const isWarning = semantic.semantic === 'attention';
                return (
                  <div key={cap.capability_id} className="flex justify-between items-center py-2 border-b border-white/10">
                    <span className="text-sm font-bold text-white/80">{cap.label}</span>
                    <span className={`inline-flex items-center gap-1 text-[10px] border px-2 py-0.5 rounded-[8px] font-extrabold uppercase ${
                      isCritical ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                      isWarning ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                      'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                    }`}>
                      {isCritical ? <AlertCircle className="w-3 h-3" /> :
                       isWarning ? <AlertTriangle className="w-3 h-3" /> :
                       <CheckCircle2 className="w-3 h-3" />}
                      {cap.state}
                    </span>
                  </div>
                );
              })}
              {!runtimeStatus && (
                <div className="text-sm text-white/50 text-center py-2">Loading capabilities...</div>
              )}
            </div>
          </div>

          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] text-white">
            <h3 className="text-base font-extrabold text-white mb-6 flex items-center gap-2">
              <Shield className="w-5 h-5 text-[var(--color-accent)]" /> Connectors Status
            </h3>
            <div className="space-y-4">
              {!runtimeStatus && (
                <div className="text-sm text-white/50 text-center py-2">Loading connectors...</div>
              )}
              {runtimeStatus?.connectors.map(conn => {
                const isError = conn.state === 'error' || conn.state === 'disconnected';
                return (
                  <div key={conn.connector_id} className="border-b border-white/10 pb-4 last:border-0">
                    <div className="flex justify-between items-center mb-2">
                      <span className="text-sm font-bold text-white/90">{conn.name}</span>
                      <span className={`inline-flex items-center gap-1 text-[10px] border px-2 py-0.5 rounded-[8px] font-extrabold uppercase ${
                        isError ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                        conn.state === 'warning' ? 'bg-amber-500/20 text-amber-400 border-amber-500/30' :
                        'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                      }`}>
                        {isError ? <AlertCircle className="w-3 h-3" /> :
                         conn.state === 'warning' ? <AlertTriangle className="w-3 h-3" /> :
                         <CheckCircle2 className="w-3 h-3" />}
                        {conn.state}
                      </span>
                    </div>
                    
                    {isError && conn.failure_code && (
                      <div className="mt-2 p-2 bg-red-500/10 border border-red-500/20 rounded-md">
                        <p className="text-[10px] text-red-400 font-bold uppercase tracking-wider">Failure Code: {conn.failure_code}</p>
                      </div>
                    )}
                    
                    <div className="flex justify-between text-[10px] text-white/50 font-medium mt-2">
                      <span>Latency: {conn.latency_ms != null ? `${conn.latency_ms}ms` : 'N/A'}</span>
                      {conn.last_sync && <span>Last Sync: {new Date(conn.last_sync).toLocaleTimeString()}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="bg-gradient-to-br from-[var(--color-surface-raised)] to-[var(--color-surface)] border border-white/10 rounded-[24px] p-8 shadow-[0_20px_50px_rgba(46,16,85,0.3)] flex-1 text-white">
            <h3 className="text-base font-extrabold text-white mb-6 flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-[var(--color-semantic-positive)]" /> Action Dispatch History
            </h3>
            <div className="space-y-4 max-h-[360px] overflow-y-auto custom-scrollbar pr-2">
              {auditLogs.length === 0 ? (
                <div className="text-xs font-semibold text-[#9CA3AF] py-8 text-center border-2 border-dashed border-white/20 rounded-[16px]">
                  No approved actions dispatched yet.
                </div>
              ) : (
                auditLogs.map((log, index) => (
                  <div key={log.id || index} className="border border-white/10 rounded-[16px] p-4 bg-white/5 hover:border-emerald-500/50 transition-all premium-shadow">
                    <div className="flex items-center justify-between mb-2">
                      <span className="inline-flex items-center gap-1 text-[9px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-[8px] font-black uppercase tracking-wider">
                        <Check className="w-2.5 h-2.5" /> Dispatched
                      </span>
                      <span className="text-[10px] text-[#9CA3AF] font-bold">
                        {new Date(log.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                    <p className="text-xs font-bold text-white leading-relaxed">
                      {log.action.replace('Approved Sentinel Action: ', '')}
                    </p>
                    <div className="flex items-center justify-between mt-3 pt-2 border-t border-white/10 text-[10px] text-white/70">
                      <span>User: <strong className="text-white">{log.user_email}</strong></span>
                      <span className="font-mono text-[9px] text-[#2563EB]">{log.resource}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
