import { useState } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useDroppable } from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Lead, Stage } from "../lib/api";
import { leadsApi } from "../lib/api";
import LeadCard from "./LeadCard";
import LeadModal from "./LeadModal";

const STAGES: {
  id: Stage;
  label: string;
  border: string;
  dotBg: string;
  glow: string;
  description: string;
}[] = [
  { id: "new",       label: "Nouveau",  border: "border-[#2a2a2a]",    dotBg: "bg-slate-600",   glow: "",                           description: "Ajouté, pas encore contacté" },
  { id: "contacted", label: "Contacté", border: "border-[#2a2a2a]",   dotBg: "bg-brand-500",   glow: "",                                       description: "DM envoyé, en attente de réponse" },
  { id: "replied",   label: "Répondu",  border: "border-[#2a2a2a]",   dotBg: "bg-brand-400",   glow: "",                                       description: "Le lead a répondu" },
  { id: "booked",    label: "Réservé",  border: "border-emerald-900", dotBg: "bg-emerald-500", glow: "",                           description: "Appel réservé sur Calendly" },
  { id: "closed",    label: "Clôturé",  border: "border-[#2a2a2a]",    dotBg: "bg-slate-500",   glow: "",                           description: "Affaire gagnée ou perdue" },
];

function Column({ stage, leads, onCardClick }: { stage: typeof STAGES[0]; leads: Lead[]; onCardClick: (l: Lead) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id });

  const avgScore = leads.length
    ? (leads.reduce((s, l) => s + (l.qualification_score || 0), 0) / leads.length).toFixed(1)
    : null;

  return (
    <div className={`flex flex-col rounded-2xl border ${stage.border} ${stage.glow} bg-[#1a1a1a]/50 min-w-[250px] w-[250px] flex-shrink-0 transition-shadow`}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#2a2a2a]/60">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${stage.dotBg}`} />
          <span className="text-[11px] font-bold text-slate-400 uppercase tracking-widest font-heading">{stage.label}</span>
        </div>
        <div className="flex items-center gap-2">
          {avgScore && (
            <span className="text-[10px] text-slate-600 font-mono">{avgScore} moy</span>
          )}
          <span className="text-[10px] bg-[#2a2a2a] text-slate-400 px-2 py-0.5 rounded-full tabular-nums">{leads.length}</span>
        </div>
      </div>

      <div
        ref={setNodeRef}
        className={`flex-1 p-3 space-y-2 min-h-[120px] rounded-b-2xl transition-all ${
          isOver ? "bg-white/[0.03] ring-1 ring-inset ring-white/[0.05]" : ""
        }`}
      >
        <SortableContext items={leads.map(l => l.id)} strategy={verticalListSortingStrategy}>
          {leads.map(lead => (
            <LeadCard key={lead.id} lead={lead} onClick={() => onCardClick(lead)} />
          ))}
        </SortableContext>
        {leads.length === 0 && (
          <div className="h-16 rounded-xl border border-dashed border-[#2a2a2a] flex items-center justify-center">
            <p className="text-[10px] text-slate-700">{stage.description}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function KanbanBoard({ leads }: { leads: Lead[] }) {
  const qc = useQueryClient();
  const [activeId, setActiveId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Lead | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } })
  );

  const moveStage = useMutation({
    mutationFn: ({ id, stage }: { id: number; stage: Stage }) =>
      leadsApi.update(id, { stage }),
    onMutate: async ({ id, stage }) => {
      await qc.cancelQueries({ queryKey: ["leads"] });
      const prev = qc.getQueryData<Lead[]>(["leads"]);
      qc.setQueryData<Lead[]>(["leads"], old =>
        old?.map(l => l.id === id ? { ...l, stage } : l) ?? []
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(["leads"], ctx.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const activeLead = activeId ? leads.find(l => l.id === activeId) : null;

  function onDragStart({ active }: DragStartEvent) { setActiveId(active.id as number); }

  function onDragEnd({ active, over }: DragEndEvent) {
    setActiveId(null);
    if (!over) return;
    const lead = leads.find(l => l.id === active.id);
    if (!lead) return;
    const newStage =
      STAGES.find(s => s.id === over.id)?.id ??
      leads.find(l => l.id === over.id)?.stage;
    if (newStage && newStage !== lead.stage) {
      moveStage.mutate({ id: lead.id, stage: newStage });
    }
  }

  return (
    <>
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <div className="flex gap-4 overflow-x-auto pb-4 scrollbar-thin h-full items-start">
          {STAGES.map(stage => (
            <Column
              key={stage.id}
              stage={stage}
              leads={leads.filter(l => l.stage === stage.id)}
              onCardClick={setSelected}
            />
          ))}
        </div>
        <DragOverlay dropAnimation={null}>
          {activeLead && (
            <div className="rotate-1 opacity-90 w-[250px]">
              <LeadCard lead={activeLead} onClick={() => {}} />
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {selected && (
        <LeadModal
          lead={leads.find(l => l.id === selected.id) ?? selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
