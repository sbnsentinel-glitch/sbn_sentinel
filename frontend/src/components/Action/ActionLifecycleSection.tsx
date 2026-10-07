/**
 * D6.9: ActionLifecycleSection
 * The top-level D6 workspace section rendered inside SignalsDetailView.
 * Fetches GET /api/v1/actions/lifecycle/{decision_id} and renders:
 *   - ContinuityNotice (if unavailable)
 *   - For each action: ActionSummary + ExecutionAttemptHistory + OutcomeSummary
 *   - CreateActionControls (if can_create)
 *   - ExecuteActionControls (if can_execute or can_retry)
 *
 * INVARIANT: decision_id comes from current_decision.decision_id only.
 * Never uses signal_id as a decision_id proxy.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Loader, Zap, RotateCcw } from 'lucide-react';

import type { ActionLifecycleDTO } from '@/types/actionLifecycle';
import { fetchActionLifecycle } from '@/utils/actionLifecycle';
import { ActionSummary } from '@/components/Action/ActionSummary';
import { ExecutionAttemptHistory } from '@/components/Action/ExecutionAttemptHistory';
import { CreateActionControls, ExecuteActionControls } from '@/components/Action/ActionControls';
import { OutcomeSummary } from '@/components/Outcome/OutcomeSummary';
import { ContinuityNotice } from '@/components/Outcome/ContinuityNotice';
interface Props {
  /** Human Decision ID — must come from current_decision.decision_id */
  decisionId: string | null | undefined;
}
export const ActionLifecycleSection: React.FC<Props> = ({ decisionId }) => {
  const [lifecycle, setLifecycle] = useState<ActionLifecycleDTO | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!decisionId) {
      setLifecycle(null);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchActionLifecycle(decisionId);
      setLifecycle(data);
    } finally {
      setLoading(false);
    }
  }, [decisionId]);

  useEffect(() => {
    load();
  }, [load]);
  if (!decisionId) {
    return (
      <div className="text-[11px] text-white/30 text-center py-4 border border-dashed border-white/10 rounded-[10px]">
        No Human Decision recorded yet. Action Lifecycle unavailable.
      </div>
    );
  }
  // --- Loading ---
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-6">
        <Loader className="w-4 h-4 text-white/40 animate-spin" />
        <span className="text-[11px] text-white/40 font-semibold">Loading action lifecycle…</span>
      </div>
    );
  }
  // --- Unavailable or continuity broken ---
  if (!lifecycle || lifecycle.technical_state !== 'ready') {
    return (
      <ContinuityNotice
        decisionId={decisionId}
        reason={
          lifecycle?.technical_state === 'unauthorized'
            ? 'Insufficient authority to view this lifecycle.'
            : lifecycle?.technical_state === 'ambiguous'
              ? 'Ambiguous record state detected.'
              : undefined
        }
      />
    );
  }
  const hasActions = lifecycle.actions.length > 0;
  return (
    <div className="space-y-5">
      {/* Section header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-[8px] bg-violet-500/20">
            <Zap className="w-3.5 h-3.5 text-violet-400" />
          </span>
          <p className="text-xs font-extrabold text-white uppercase tracking-wide">
            Governed Action Lifecycle
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          title="Refresh lifecycle"
          className="p-1.5 rounded-[8px] bg-white/5 hover:bg-white/10 transition-colors disabled:opacity-40 cursor-pointer"
        >
          <RotateCcw className={`w-3.5 h-3.5 text-white/50 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Journey ID */}
      {lifecycle.journey_id && (
        <p className="text-[9px] font-mono text-white/25">
          Journey: {lifecycle.journey_id}
        </p>
      )}

      {/* No actions yet — show Create controls if eligible */}
      {!hasActions && (
        <div className="text-[11px] text-white/40 py-3 border border-dashed border-white/10 rounded-[10px] text-center">
          No governed actions created for this decision yet.
        </div>
      )}

      {/* Action items */}
      {lifecycle.actions.map((action) => (
        <div key={action.action_id} className="border border-white/10 rounded-[14px] p-4 space-y-4 bg-black/20">
          {/* 1. Action identity + status */}
          <ActionSummary action={action} />

          {/* 2. Execution Attempt History */}
          <div>
            <p className="text-[10px] text-white/40 uppercase tracking-widest font-bold mb-2">
              Execution Attempts ({action.attempts.length})
            </p>
            <ExecutionAttemptHistory attempts={action.attempts} />
          </div>

          {/* 3. Outcome — explicitly show pending if missing */}
          <div>
            <p className="text-[10px] text-white/40 uppercase tracking-widest font-bold mb-2">
              Operational Outcome
            </p>
            {action.outcome ? (
              <OutcomeSummary outcome={action.outcome} />
            ) : (
              <div className="bg-white/5 border border-white/10 rounded-[12px] p-4 flex items-center gap-3">
                <span className="p-1.5 rounded-[8px] bg-amber-500/20">
                  <Loader className="w-4 h-4 text-amber-400 animate-spin" />
                </span>
                <div>
                  <p className="text-xs font-extrabold text-white uppercase tracking-wide">Pending Confirmation</p>
                  <p className="text-[10px] text-white/40">Waiting for authoritative verification of external state.</p>
                </div>
              </div>
            )}
          </div>

          {/* 4. Execute / Retry controls */}
          <ExecuteActionControls action={action} onSuccess={load} />
        </div>
      ))}

      {/* Create new action — only if backend says eligible */}
      {lifecycle.can_create && (
        <CreateActionControls
          decisionId={decisionId}
          creation={lifecycle.creation}
          onSuccess={load}
        />
      )}
    </div>
  );
};
