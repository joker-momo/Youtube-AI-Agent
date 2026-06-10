/**
 * Checklist graphic — calm typographic list (taste refresh C3).
 *
 * No per-row cards or drop shadows: each item is a thin ghost check + large
 * text sitting directly on the warm background, revealed in a quick gentle
 * cascade. One dominant idea, readable within ~1s, no dashboard feel.
 */
import React from 'react';
import {getGraphicColors, graphicTheme, type GraphicColorRoles} from './graphic-theme';
import {GraphicChecklistPayload} from './graphic-payloads';
import {GraphicFrame, useReveal} from './GraphicFrame';

const {font, fontSize, motion} = graphicTheme;

/** Thin outlined check — a quiet mark, not a filled badge. */
const GhostCheck: React.FC<{colors: GraphicColorRoles}> = ({colors}) => (
  <svg width={52} height={52} viewBox="0 0 52 52" style={{flexShrink: 0}}>
    <circle cx={26} cy={26} r={23} fill="none" stroke={colors.positive} strokeWidth={2.5} opacity={0.55} />
    <path
      d="M16 27 L23 34 L37 18"
      fill="none"
      stroke={colors.positive}
      strokeWidth={4}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const ChecklistItem: React.FC<{
  children: React.ReactNode;
  colors: GraphicColorRoles;
  delaySec: number;
}> = ({children, colors, delaySec}) => {
  const reveal = useReveal(delaySec);
  return (
    <div style={{...reveal, display: 'flex', alignItems: 'center', gap: 28, width: '100%'}}>
      <GhostCheck colors={colors} />
      <span
        style={{
          fontFamily: font.family,
          fontSize: fontSize.item,
          fontWeight: 600,
          color: colors.textPrimary,
          lineHeight: 1.16,
          // Keep each item to ~2 readable lines, no dense wrapping.
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {children}
      </span>
    </div>
  );
};

export const GraphicChecklist: React.FC<{
  payload: GraphicChecklistPayload;
  durationInFrames: number;
  fps: number;
}> = ({payload}) => {
  const colors = getGraphicColors(payload.variant);
  const items = payload.items.slice(0, 5);
  return (
    <GraphicFrame
      title={payload.title}
      footer={payload.footer}
      variant={payload.variant}
      surfaceStyle={payload.surface_style}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 44,
          width: '100%',
          maxWidth: 840,
          margin: '0 auto',
        }}
      >
        {items.map((item, i) => (
          <ChecklistItem key={i} colors={colors} delaySec={motion.itemStartSec + i * motion.itemStaggerSec}>
            {item}
          </ChecklistItem>
        ))}
      </div>
    </GraphicFrame>
  );
};
